"""Capability installs pull the substrate's DEFERRED conflict check forward.

The substrate's pypi lane skips the manifest re-solve by contract and leaves
the conflict check to the snapshot's own solve. So a leaf that contradicts the
base's pins installs "successfully" into the pip overlay — shadowing a pinned
dependency — and only fails much later when a snapshot is minted. Live incident
2026-07-26: one such add left a project's default session permanently
un-snapshottable, and every remote python step silently degraded for hours.

Two behaviours guarded here:
  1. `solve_at_add=True` → the substrate is asked to solve AT ADD TIME
     (weft `fast=False`), so the conflict raises where `ensure_capability` can
     route the package into an isolated env, and NOTHING is recorded (the
     project's session stays clean).
  2. the overlay's `shadows_base` warning — the earliest signal, previously
     DROPPED by the envelope — is surfaced to the agent.

ARMED: the fake substrate records the `fast` kwarg it actually received, so a
regression that stops sending it fails the assertion rather than passing
vacuously. WIDE: covers the pre-lever substrate (TypeError → degrade, not
crash), the no-conflict happy path, and the ceiling that a bulk/internal
install still uses the fast lane.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from core.compute.errors import ComputeError  # noqa: E402


class _Ad:
    """A fake substrate adapter recording how ensure_available was called."""

    def __init__(self, *, raise_conflict=False, shadows=None, no_fast_kwarg=False):
        self.calls: list[dict] = []
        self._raise = raise_conflict
        self._shadows = shadows
        self._no_fast = no_fast_kwarg

    def ensure_available(self, target, request, verify=None, **kw):
        if self._no_fast and "fast" in kw:
            raise TypeError("ensure_available() got an unexpected keyword 'fast'")
        self.calls.append({"target": target, "request": request, **kw})
        if self._raise:
            raise ComputeError(
                "env.solve_conflict", "spec 'aba-p-default-python' is "
                "unsatisfiable as pinned", stage="solve",
                hints={"solver_message": "PKG needs D==1.6; base pins D==1.9",
                       "at": "add-time (fast=False)"})
        attempt = {"lane": "pypi", "outcome": "installed"}
        if self._shadows:
            attempt["shadows_base"] = self._shadows
        return {"satisfied": True, "changed": True, "attempts": [attempt],
                "verified": {}, "runtime": {"prefix": "/tmp/p"}}


@pytest.fixture()
def penv(monkeypatch):
    """project_env with its session/registry side effects stubbed out."""
    from core.compute import named_envs, project_env
    rows: dict = {"additions": [], "rev": 0}
    monkeypatch.setattr(project_env, "ensure",
                        lambda pid, lang: {"session_id": "ses_1",
                                           "runtime": {"prefix": "/tmp/p"}})
    monkeypatch.setattr(project_env, "get", lambda pid, lang: rows)
    monkeypatch.setattr(project_env, "_save_row", lambda *a, **k: None)
    monkeypatch.setattr(project_env, "_current_runtime", lambda sid: {"prefix": "/tmp/p"})
    monkeypatch.setattr(named_envs, "_sync", lambda v: v)
    return project_env, rows


def test_solve_at_add_asks_the_substrate_to_solve_now(penv, monkeypatch):
    project_env, rows = penv
    ad = _Ad()
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    project_env.install("p1", "python", ["pkg"], eco="pypi", solve_at_add=True)
    assert ad.calls, "ARMED: the substrate must have been called"
    assert ad.calls[0]["fast"] is False, \
        "solve_at_add=True must reach the substrate as fast=False"


def test_default_install_keeps_the_fast_lane(penv, monkeypatch):
    """CEILING: only the agent-facing capability lane pays for a full solve;
    internal/bulk adds must not silently get seconds slower."""
    project_env, _ = penv
    ad = _Ad()
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    project_env.install("p1", "python", ["pkg"], eco="pypi")
    assert "fast" not in ad.calls[0], "no solve_at_add → no fast kwarg at all"


def test_add_time_conflict_raises_and_records_nothing(penv, monkeypatch):
    """The whole point: the conflict lands HERE, and the project's session is
    left untouched — no addition, no rev bump, so it stays snapshottable."""
    project_env, rows = penv
    ad = _Ad(raise_conflict=True)
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    with pytest.raises(ComputeError) as ei:
        project_env.install("p1", "python", ["pkg"], eco="pypi", solve_at_add=True)
    assert ei.value.code == "env.solve_conflict"
    assert rows["additions"] == [], "a failed add must not be recorded"
    assert rows["rev"] == 0, "a failed add must not bump the rev"


def test_conflict_is_recognized_by_the_isolation_router():
    """The raise must match the predicate that routes to an isolated env —
    otherwise the fix moves the error earlier but nobody acts on it."""
    from content.bio.tools.discovery import _is_constraint_conflict
    e = ComputeError("env.solve_conflict", "spec 'x' is unsatisfiable as pinned",
                     stage="solve")
    assert _is_constraint_conflict(str(e))


def test_shadows_base_is_surfaced(penv, monkeypatch):
    """The dropped-signal regression: the overlay's shadow warning must reach
    the caller so the agent knows the running stack is not the pinned base."""
    project_env, _ = penv
    ad = _Ad(shadows="scikit-learn 1.9.0 -> 1.6.0")
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    out = project_env.install("p1", "python", ["pkg"], eco="pypi", solve_at_add=True)
    assert out.get("shadows_base") == "scikit-learn 1.9.0 -> 1.6.0"


def test_no_shadow_key_when_nothing_shadowed(penv, monkeypatch):
    """WIDE — the other side: a clean install must not invent the warning."""
    project_env, _ = penv
    ad = _Ad()
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    out = project_env.install("p1", "python", ["pkg"], eco="pypi", solve_at_add=True)
    assert "shadows_base" not in out


def test_pre_lever_substrate_degrades_instead_of_crashing(penv, monkeypatch):
    """WIDE — the degenerate deployment: an older substrate has no fast= on the
    tagged verb. The install must still work (the conflict then surfaces at
    snapshot time, which is now LOUD), never TypeError into the agent's face."""
    project_env, rows = penv
    ad = _Ad(no_fast_kwarg=True)
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: ad)
    out = project_env.install("p1", "python", ["pkg"], eco="pypi", solve_at_add=True)
    assert out["satisfied"] is True
    assert ad.calls and "fast" not in ad.calls[0], "retried without the kwarg"
    assert rows["rev"] == 1, "the successful install IS recorded"


def test_every_agent_facing_install_solves_at_add():
    """The guard above was written for the python/pypi lane after the
    2026-07-26 incident and never crossed to R — so the R lanes kept
    DEFERRING the solve, and a capability install that contradicted the
    base left the project's R session un-snapshottable exactly as pypi
    once did. The consequence only shows up later and elsewhere: the
    live session keeps working, and the next BACKGROUND R job fails to
    mint an EnvID (field report, 2026-08).

    This is a CALLER claim, so it needs caller coverage — the behavioural
    test above pins project_env.install's end of the contract, and cannot
    see a call site that never asks. Every agent-facing capability install
    in discovery.py must ask for the solve; a new lane that forgets fails
    here rather than in someone's background job.
    """
    import ast
    import pathlib

    src = pathlib.Path("backend/content/bio/tools/discovery.py")
    tree = ast.parse(src.read_text())
    missing, seen = [], 0
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr == "install"
                and isinstance(f.value, ast.Name)
                and f.value.id in ("project_env", "_penv")):
            continue
        seen += 1
        kw = {k.arg: k for k in n.keywords}
        ok = (isinstance(kw.get("solve_at_add"), ast.keyword)
              and isinstance(kw["solve_at_add"].value, ast.Constant)
              and kw["solve_at_add"].value.value is True)
        if not ok:
            eco = kw.get("eco")
            eco_s = (eco.value.value if eco is not None
                     and isinstance(eco.value, ast.Constant) else "?")
            missing.append(f"{src.name}:{n.lineno} (eco={eco_s!r})")

    assert seen >= 4, f"expected the known install lanes, found {seen}"
    assert not missing, (
        "agent-facing capability install(s) still DEFER the conflict check, "
        "leaving the session un-snapshottable: " + ", ".join(missing))
