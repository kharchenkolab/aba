"""Env-resolution honesty: a step that cannot GET its declared environment
must FAIL, never be relocated to whatever interpreter sits on the node's PATH.

The bug this guards (live, 2026-07-26): one pypi add left a project's default
session un-snapshottable. Both consumers of the env identity swallowed the
resulting `env.solve_conflict` and ran the step on the remote node's system
python 3.8 — with a broken user-site package — for hours, reported as success.
`env_grade: node-system` must be reachable ONLY through the explicit
`env='system'` lever.

ARMED: each test asserts the precondition actually fired (the snapshot was
consulted and raised) — a run where nothing raised would not prove anything, so
the fake records its calls and the tests assert on them. WIDE: covers the
degenerate shapes — `env=None` + remote site (the COMMON shape, and the one
that broke), an unknown named env, a NON-env failure (must still degrade to the
one-shot lane), and the explicit system lever (must still work).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from core.compute.errors import ComputeError, is_env_resolution_failure  # noqa: E402


# ── the policy predicate (one definition, two consumers) ─────────────────────

def test_policy_classifies_env_families_and_spares_transport():
    for code in ("env.solve_conflict", "env.solve_failed", "env.platform_mismatch",
                 "env.unavailable_in_lanes", "session.cold_base", "no_base_pack"):
        assert is_env_resolution_failure(ComputeError(code, "x")), code
    # transport / capacity / task problems are NOT env failures — those may
    # legitimately fall back to another lane
    for code in ("site.unreachable", "task.capacity", "task.invalid",
                 "internal.error", "data.missing"):
        assert not is_env_resolution_failure(ComputeError(code, "x")), code
    # an UNTYPED exception on the env path must not degrade (that is exactly
    # the shape the old `except Exception: return None, None` ate)
    assert is_env_resolution_failure(RuntimeError("boom"))
    assert is_env_resolution_failure(KeyError("runtime"))


# ── the detached/one-shot choke point ────────────────────────────────────────

def _submitter(site="siteA"):
    from core.jobs.weft_submitter import WeftSubmitter
    s = WeftSubmitter.__new__(WeftSubmitter)     # no substrate needed
    s.site = site
    return s


def _break_snapshot(monkeypatch, exc):
    """Make the env identity unresolvable, and RECORD that it was consulted."""
    calls: list = []
    from core.compute import base_env, project_env

    def snap(pid, lang):
        calls.append((pid, lang))
        raise exc
    monkeypatch.setattr(project_env, "snapshot", snap)
    monkeypatch.setattr(base_env, "require", lambda lang: "pack")
    return calls


def test_unresolvable_env_raises_instead_of_node_system(monkeypatch):
    """THE regression. env=None + remote site + a failing snapshot must raise,
    and the raise must carry the substrate's diagnosis."""
    calls = _break_snapshot(monkeypatch, ComputeError(
        "env.solve_conflict", "spec 'aba-p-default-python' is unsatisfiable as pinned",
        stage="solve", hints={"solver_message": "PKG depends on D==1.6 and D==1.9"}))
    s = _submitter()
    with pytest.raises(ComputeError) as ei:
        s._detached_env({}, "p1", "python")
    assert calls == [("p1", "python")], "ARMED: the snapshot must have been consulted"
    err = ei.value
    assert err.code == "env.unresolved"
    assert "env.solve_conflict" in str(err.hints.get("cause"))
    # weft's diagnosis survives the crossing — this is what was lost before
    assert "unsatisfiable as pinned" in err.detail
    assert err.hints["substrate_hints"]["solver_message"]
    assert "levers" in err.hints and len(err.hints["levers"]) >= 3


def test_untyped_snapshot_failure_also_raises(monkeypatch):
    """WIDE — the degenerate shape: a non-ComputeError on the env path (a
    KeyError in the registry, an offline adapter) must not degrade either."""
    calls = _break_snapshot(monkeypatch, KeyError("session_id"))
    with pytest.raises(ComputeError) as ei:
        _submitter()._detached_env({}, "p1", "python")
    assert calls, "ARMED: snapshot consulted"
    assert ei.value.code == "env.unresolved"
    assert ei.value.hints["cause"] == "KeyError"


def test_no_base_pack_raises_not_degrades(monkeypatch):
    """A deployment with no declared base pack is a misconfiguration, and
    base_env.require's own contract says 'never a silent downgrade'."""
    from core.compute import base_env, project_env

    def boom(lang):
        raise ComputeError("no_base_pack", f"no base pack for {lang!r}")
    monkeypatch.setattr(base_env, "require", boom)
    monkeypatch.setattr(project_env, "snapshot",
                        lambda *a, **k: pytest.fail("must not reach snapshot"))
    with pytest.raises(ComputeError) as ei:
        _submitter()._detached_env({}, "p1", "python")
    assert ei.value.code == "env.unresolved"


def test_unknown_named_env_refuses(monkeypatch):
    """Asking for an env we cannot find is a refusal, not a licence to run on
    the node's interpreter (this used to print a line and fall through)."""
    from core.compute import named_envs
    monkeypatch.setattr(named_envs, "resolve", lambda pid, name: None)
    with pytest.raises(ComputeError) as ei:
        _submitter()._detached_env({"env": "myenv"}, "p1", "python")
    assert ei.value.code == "env.unknown"
    assert "make_isolated_env" in str(ei.value.hints)


def test_explicit_system_lever_still_yields_node_interpreter(monkeypatch):
    """The CEILING: env='system' is the one sanctioned path to the node's own
    interpreter, and it must not consult (or need) the project env at all."""
    from core.compute import project_env
    monkeypatch.setattr(project_env, "snapshot",
                        lambda *a, **k: pytest.fail("system lever must not snapshot"))
    for lever in ("system", "none", "System", "NONE"):
        assert _submitter()._detached_env({"env": lever}, "p1", "python") == (None, None)


def test_named_env_resolves_normally(monkeypatch):
    """Ceiling on the other side: a KNOWN named env still resolves to its
    EnvID — the fix must not turn working installs into failures."""
    from core.compute import named_envs
    monkeypatch.setattr(named_envs, "resolve",
                        lambda pid, name: {"env_id": "env:v1:abc"})
    assert _submitter()._detached_env({"env": "myenv"}, "p1", "python") \
        == ("env:v1:abc", "myenv")


# ── the interactive remote lane ──────────────────────────────────────────────

def _fake_pool(exc):
    """A kernel pool whose get_or_start raises `exc`; records the attempt."""
    calls: list = []

    class _Pool:
        def get_or_start(self, *a, **k):
            calls.append(k)
            raise exc
    return _Pool(), calls


def _remote_kernel(monkeypatch, exc, env=None):
    from content.bio.tools import run_exec
    from core.exec import kernels
    pool, calls = _fake_pool(exc)
    monkeypatch.setattr(kernels, "get_pool", lambda: pool)
    monkeypatch.setattr("core.compute.named_envs.resolve_env",
                        lambda pid, lang, explicit=None: env)
    out = run_exec._run_remote_kernel({"code": "1", "env": env}, None,
                                      "p1", "t1", "siteA")
    return out, calls


def test_interactive_env_failure_surfaces_and_does_not_fall_back(monkeypatch):
    """An env failure must NOT return None: None sends the step to the one-shot
    lane, which is exactly where it used to become a node-system run."""
    out, calls = _remote_kernel(monkeypatch, ComputeError(
        "env.solve_conflict", "unsatisfiable as pinned", stage="solve",
        hints={"solver_message": "conflict"}))
    assert calls, "ARMED: the pool must have been asked to start a kernel"
    assert out is not None, "env failure must not degrade to the one-shot lane"
    assert out["status"] == "error" and out["error"] == "env.solve_conflict"
    assert "NOT relocated" in out["note"]
    assert out["hints"]["solver_message"] == "conflict"


def test_interactive_non_env_failure_still_falls_back(monkeypatch):
    """WIDE — the other side: a genuine "no kernel on this site" condition must
    STILL fall back to the one-shot lane. Over-refusing would break remote work
    on sites that simply cannot host a persistent session."""
    out, calls = _remote_kernel(monkeypatch,
                                ComputeError("site.unreachable", "down"))
    assert calls, "ARMED: pool consulted"
    assert out is None, "a transport/capacity failure keeps the one-shot fallback"
