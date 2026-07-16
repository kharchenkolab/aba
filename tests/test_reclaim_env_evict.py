"""Reclaim-disk fix: a pack-backed module's "Reclaim disk space" evicts its weft env (frees the
real bytes) instead of rmtree'ing the pre-weft `$TOOLS_ENV` path — which no longer exists on the
weft branch, so the old rmtree was a silent no-op that left the env in place (and probe_ready reads
the weft store, so the module still showed present). See misc/modules.md + the reclaim bug note.

Standalone harness (no pytest import → runs under PYTHONNOUSERSITE=1, dodging the ~/.local numcodecs
break); also pytest-collectable.

Run: python tests/test_reclaim_env_evict.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_HOME", tempfile.mkdtemp(prefix="aba_reclaim_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.modules.registry as reg      # noqa: E402
import core.modules.state as st          # noqa: E402
import core.modules.manager as mgr       # noqa: E402
import core.modules.reconciler as rec    # noqa: E402


class _FakeCompute:
    """Stateful weft double. `blockers` is the live in_use list env_evict refuses on; a
    `session_stop` removes that session from it (mirrors weft), so once the safe holders are
    stopped a retried env_evict succeeds — the real selective-stop-then-evict flow."""
    def __init__(self, envs=None, boom=None, blockers=None):
        self.calls = []
        self._envs = envs or []
        self._boom = boom            # legacy: a fixed exception to raise from env_evict, or None
        self._blockers = list(blockers) if blockers is not None else None

    def _evict_error(self):
        from core.compute.errors import ComputeError
        return ComputeError(
            "env.evict_blocked",
            f"{len(self._blockers)} live job(s)/session(s)/kernel(s) use this env",
            stage="infra", hints={"in_use": list(self._blockers)})

    def sync_call(self, name, *a):
        self.calls.append((name, a))
        if name == "env_evict":
            if self._boom is not None:
                raise self._boom
            if self._blockers:
                raise self._evict_error()
            return {"ok": True}
        if name == "session_stop" and self._blockers is not None:
            sid = a[0]
            self._blockers = [b for b in self._blockers if b.get("id") != sid]
            return {"ok": True}
        if name == "list_envs":
            return {"envs": self._envs}
        return {"ok": True}


def test_pack_env_id_resolves_local_env(monkeypatch):
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_for", lambda s: "r-bio")
    w = _FakeCompute(envs=[{"name": "python-bio", "env_id": "e1"},
                           {"name": "r-bio", "env_id": "env:v1:rbio"}])
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    assert mgr.pack_env_id(reg.get("r-bio")) == ("env:v1:rbio", "local")


def test_pack_env_id_none_when_not_pack_backed(monkeypatch):
    monkeypatch.setattr(mgr, "pack_for", lambda s: None)   # substrate offline / shell module
    assert mgr.pack_env_id(reg.get("r-bio")) is None


def test_remove_artifacts_pack_backed_evicts_weft_env(monkeypatch):
    """The fix: reclaim on a pack-backed module env_evicts its weft EnvID (frees real bytes),
    instead of rmtree'ing the pre-weft $TOOLS_ENV path that no longer exists (the no-op bug)."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    w = _FakeCompute()
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    out = rec._remove_artifacts(reg.get("r-bio"), log=lambda *_: None)
    assert ("env_evict", ("env:v1:rbio", "local")) in w.calls
    assert out["reclaimed"] is True and out["env_id"] == "env:v1:rbio"


def test_remove_artifacts_stops_kernelless_holder_then_evicts(monkeypatch):
    """P1/#3 selective stop: a kernel-less session only holds the base env open (no running
    block). Reclaim stops it (session_stop) and the retried env_evict succeeds — the whole
    point of the fix (before, an idle kernel-less session wedged reclaim forever)."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    w = _FakeCompute(blockers=[{"kind": "session", "id": "ses_idle",
                                "has_kernel": False, "idle_s": 21000}])
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    out = rec._remove_artifacts(reg.get("r-bio"), log=lambda *_: None)
    assert ("session_stop", ("ses_idle",)) in w.calls          # stopped the safe holder
    assert w.calls.count(("env_evict", ("env:v1:rbio", "local"))) == 2   # tried, then retried
    assert out["reclaimed"] is True and out["env_id"] == "env:v1:rbio"


def test_remove_artifacts_live_kernel_holder_surfaced_not_killed(monkeypatch):
    """A session WITH a running kernel (or a running kernel/job) is live work — never killed
    for a disk reclaim. Reclaim can't proceed; the reason is surfaced honestly, not swallowed."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    w = _FakeCompute(blockers=[{"kind": "session", "id": "ses_live", "has_kernel": True},
                               {"kind": "kernel", "id": "krn_live"}])
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    out = rec._remove_artifacts(reg.get("r-bio"), log=lambda *_: None)   # must NOT raise
    assert not any(c[0] == "session_stop" for c in w.calls)    # nothing safe → nothing stopped
    assert out["reclaimed"] is False
    assert {h["id"] for h in out["in_use"]} == {"ses_live", "krn_live"}


def test_remove_artifacts_mixed_stops_safe_leaves_live(monkeypatch):
    """Mixed holders: stop the kernel-less session, but a running kernel still holds the env →
    reclaim still can't finish; surface the remaining live blocker + what we stopped."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    w = _FakeCompute(blockers=[{"kind": "session", "id": "ses_idle", "has_kernel": False},
                               {"kind": "kernel", "id": "krn_live"}])
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    out = rec._remove_artifacts(reg.get("r-bio"), log=lambda *_: None)
    assert ("session_stop", ("ses_idle",)) in w.calls
    assert out["reclaimed"] is False
    assert out["stopped_sessions"] == ["ses_idle"]
    assert {h["id"] for h in out["in_use"]} == {"krn_live"}   # only the live holder remains


def test_remove_artifacts_non_pack_falls_back_to_manifest_rmtree(monkeypatch, tmp_path):
    """A shell/script module (not pack-backed) still reclaims via the manifest remove.paths."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: None)
    d = tmp_path / "toolsenv"; d.mkdir(); (d / "x").write_text("bytes")
    monkeypatch.setattr(mgr, "expand_path", lambda p: d)
    monkeypatch.setattr(compute, "get_compute",
                        lambda: (_ for _ in ()).throw(AssertionError("must not touch compute")))
    out = rec._remove_artifacts(reg.get("r-bio"), log=lambda *_: None)
    assert not d.exists()                                  # manifest path rmtree'd
    assert out["reclaimed"] is True and "removed" in out["detail"]


def test_set_mode_off_remove_evicts_pack_env_and_surfaces_outcome(monkeypatch):
    """End-to-end Reclaim button path: set_mode(off, remove=True) evicts the pack env AND
    returns the reclaim outcome on the view (never a silent no-op)."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    monkeypatch.setattr(rec, "run_module", lambda *a, **k: None)
    w = _FakeCompute()
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    view = rec.set_mode("r-bio", "off", remove=True, log=lambda *_: None)
    assert ("env_evict", ("env:v1:rbio", "local")) in w.calls
    assert st.get_desired("r-bio") == "off"
    assert view["reclaim"]["reclaimed"] is True


def test_set_mode_off_remove_blocked_notifies(monkeypatch):
    """When a LIVE holder (running kernel) blocks and can't be safely stopped, the view carries
    the blocked reason and a notify fires (UI toast) — never a silent no-op."""
    import core.compute as compute
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    w = _FakeCompute(blockers=[{"kind": "kernel", "id": "krn_live"}])
    monkeypatch.setattr(compute, "get_compute", lambda: w)
    notes = []
    monkeypatch.setattr(rec, "_notify", lambda spec, mstate, **k: notes.append(k.get("error")))
    view = rec.set_mode("r-bio", "off", remove=True, log=lambda *_: None)
    assert view["reclaim"]["reclaimed"] is False
    assert any("live" in (n or "") for n in notes)


_TESTS = [
    test_pack_env_id_resolves_local_env,
    test_pack_env_id_none_when_not_pack_backed,
    test_remove_artifacts_pack_backed_evicts_weft_env,
    test_remove_artifacts_stops_kernelless_holder_then_evicts,
    test_remove_artifacts_live_kernel_holder_surfaced_not_killed,
    test_remove_artifacts_mixed_stops_safe_leaves_live,
    test_remove_artifacts_non_pack_falls_back_to_manifest_rmtree,
    test_set_mode_off_remove_evicts_pack_env_and_surfaces_outcome,
    test_set_mode_off_remove_blocked_notifies,
]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        kw = {}
        sig = inspect.signature(t).parameters
        if "monkeypatch" in sig:
            kw["monkeypatch"] = mp
        if "tmp_path" in sig:
            kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="reclaim_"))
        # each test starts from a clean desired-state store
        try:
            st.set_desired("r-bio", None)
        except Exception:  # noqa: BLE001
            pass
        try:
            t(**kw)
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
