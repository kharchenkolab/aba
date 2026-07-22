"""Verification-probe memoization — pay the cold import once per identity.

Field finding (2026-07-22 live session): the capability layer re-derived
"does X load in env E" with a fresh-subprocess cold import on EVERY request —
24s after an install that had already succeeded, 69s on a call that installed
NOTHING (the packages were present; the entire cost was probe). The substrate
now records verification below the API (record-gating, verify-first
pre-check ~0.4s); until we adopt the verb (F-V2), this memo is the
consumer-side stopgap: a POSITIVE probe result is stable for a given
identity — (session_id, rev) for mutable sessions, EnvID for frozen envs —
so repeats are lookups, not interpreter starts.

Rules pinned here: positives only (a failure may be transient and must
re-derive); identity change re-probes (rev bump, new EnvID); no identity →
no memoization (absent shape: always probe).
"""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


@pytest.fixture(autouse=True)
def _clean_memo():
    from core.exec import verify as v
    v._PROBE_MEMO.clear()
    yield
    v._PROBE_MEMO.clear()


def _ok_proc(*a, **k):
    return types.SimpleNamespace(returncode=0, stdout="ABA_IMPORT_OK\n",
                                 stderr="")


def _fail_proc(*a, **k):
    return types.SimpleNamespace(returncode=1, stdout="",
                                 stderr="boom: import explodes")


def test_positive_probe_is_memoized_per_key(monkeypatch):
    from core.exec import verify as v
    calls: list = []

    def _run(cmd, **k):
        calls.append(cmd)
        return _ok_proc()

    monkeypatch.setattr(v.subprocess, "run", _run)
    key = ("session", "ses_1", 3)
    ok1, _ = v.verify_python_imports(["pkg_a"], memo_key=key)
    ok2, _ = v.verify_python_imports(["pkg_a"], memo_key=key)
    assert ok1 and ok2
    assert len(calls) == 1, (
        f"memo hit still spawned a subprocess ({len(calls)} runs) — the "
        f"69s-class re-derivation is back")


def test_identity_change_reprobes(monkeypatch):
    from core.exec import verify as v
    calls: list = []
    monkeypatch.setattr(v.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or _ok_proc())
    v.verify_python_imports(["pkg_a"], memo_key=("session", "ses_1", 3))
    v.verify_python_imports(["pkg_a"], memo_key=("session", "ses_1", 4))  # rev bump
    v.verify_python_imports(["pkg_a"], memo_key=("session", "ses_2", 0))  # new session
    assert len(calls) == 3, "a changed identity must re-derive"


def test_no_memo_key_always_probes(monkeypatch):
    from core.exec import verify as v
    calls: list = []
    monkeypatch.setattr(v.subprocess, "run",
                        lambda cmd, **k: calls.append(cmd) or _ok_proc())
    v.verify_python_imports(["pkg_a"])
    v.verify_python_imports(["pkg_a"])
    assert len(calls) == 2, "absent identity must never memoize"


def test_negative_results_are_never_cached(monkeypatch):
    from core.exec import verify as v
    calls: list = []

    def _run(cmd, **k):
        calls.append(cmd)
        return _fail_proc() if len(calls) == 1 else _ok_proc()

    monkeypatch.setattr(v.subprocess, "run", _run)
    key = ("session", "ses_1", 3)
    ok1, detail = v.verify_python_imports(["pkg_a"], memo_key=key)
    assert not ok1 and "boom" in detail
    ok2, _ = v.verify_python_imports(["pkg_a"], memo_key=key)
    assert ok2 and len(calls) == 2, (
        "a transient failure must re-derive on the next request")


def test_partial_hit_probes_only_pending_names(monkeypatch):
    from core.exec import verify as v
    scripts: list = []

    def _run(cmd, **k):
        scripts.append(cmd[-1])
        return _ok_proc()

    monkeypatch.setattr(v.subprocess, "run", _run)
    key = ("session", "ses_1", 3)
    v.verify_python_imports(["pkg_a"], memo_key=key)
    v.verify_python_imports(["pkg_a", "pkg_b"], memo_key=key)
    assert len(scripts) == 2
    assert "pkg_b" in scripts[1] and "pkg_a" not in scripts[1], (
        f"already-verified name re-probed: {scripts[1][:200]}")


def test_named_env_probe_memoizes_passed_only(monkeypatch):
    import content.bio.tools.discovery as d
    import core.compute.named_envs as ne
    from core.exec import verify as v
    v._PROBE_MEMO.clear()
    calls: list = []

    def _run_in(pid, name, code, **k):
        calls.append(name)
        out = "CAPQ=MISSING" if len(calls) == 1 else "CAPQ=2.0"
        return {"ok": True, "stdout": out, "stderr": "", "returncode": 0}

    monkeypatch.setattr(ne, "run_in", _run_in)
    # failed verdict → not cached → re-probes
    v1 = d._probe_named_env("p", "grow", "r", "PkgX", None, env_id="env_A")
    v2 = d._probe_named_env("p", "grow", "r", "PkgX", None, env_id="env_A")
    assert v1[0] == "failed" and v2[0] == "passed"
    assert len(calls) == 2
    # passed verdict → cached for the same identity
    v3 = d._probe_named_env("p", "grow", "r", "PkgX", None, env_id="env_A")
    assert v3[0] == "passed" and len(calls) == 2, "passed probe re-derived"
    # new identity (extend minted a new EnvID) → re-probe
    d._probe_named_env("p", "grow", "r", "PkgX", None, env_id="env_B")
    assert len(calls) == 3
    # absent identity → never memoized
    d._probe_named_env("p", "grow", "r", "PkgX", None, env_id=None)
    d._probe_named_env("p", "grow", "r", "PkgX", None, env_id=None)
    assert len(calls) == 5


def test_session_probes_pass_the_identity_key(monkeypatch):
    """The default-session callers thread (session_id, rev) as the memo key —
    the fix is inert if the busiest call sites never pass an identity."""
    import content.bio.tools.discovery as d
    keys: list = []
    import core.exec.verify as v

    def _vpi(names, memo_key=None, **k):
        keys.append(memo_key)
        return True, ""

    monkeypatch.setattr(v, "verify_python_imports", _vpi)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(
            get=lambda pid, lang: {"session_id": "ses_9", "rev": 7},
            exec_argv=lambda *a, **k: None)))
    k = d._session_probe_memo_key("prj", "python")
    assert k == ("session", "ses_9", 7), k
    # degenerate: no session row yet → None (probe, don't memoize)
    monkeypatch.setitem(sys.modules, "core.compute", types.SimpleNamespace(
        project_env=types.SimpleNamespace(get=lambda pid, lang: None)))
    assert d._session_probe_memo_key("prj", "python") is None
