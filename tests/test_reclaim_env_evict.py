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
    def __init__(self, envs=None, boom=None):
        self.calls = []
        self._envs = envs or []
        self._boom = boom            # an exception INSTANCE to raise from env_evict, or None
    def sync_call(self, name, *a):
        self.calls.append((name, a))
        if name == "env_evict" and self._boom is not None:
            raise self._boom
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


def test_remove_artifacts_blocked_surfaces_reason(monkeypatch):
    """weft refuses (live jobs/sessions/kernels use the env) → outcome is NOT swallowed:
    reclaimed=False with the detail + the in_use holders, so the user learns why."""
    import core.compute as compute
    from core.compute.errors import ComputeError
    err = ComputeError("env.evict_blocked", "3 live job(s)/session(s)/kernel(s) use this env",
                       stage="infra", hints={"in_use": [{"kind": "session", "id": "ses_1"}]})
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    monkeypatch.setattr(compute, "get_compute", lambda: _FakeCompute(boom=err))
    out = rec._remove_artifacts(reg.get("r-bio"), log=lambda *_: None)   # must NOT raise
    assert out["reclaimed"] is False
    assert "live" in out["detail"]
    assert out["in_use"] == [{"kind": "session", "id": "ses_1"}]


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
    """When weft refuses, the view carries the blocked reason and a notify fires (UI toast)."""
    import core.compute as compute
    from core.compute.errors import ComputeError
    err = ComputeError("env.evict_blocked", "2 live session(s) use this env", stage="infra",
                       hints={"in_use": [{"kind": "session", "id": "s1"}]})
    monkeypatch.setattr(mgr, "pack_env_id", lambda s: ("env:v1:rbio", "local"))
    monkeypatch.setattr(compute, "get_compute", lambda: _FakeCompute(boom=err))
    notes = []
    monkeypatch.setattr(rec, "_notify", lambda spec, mstate, **k: notes.append(k.get("error")))
    view = rec.set_mode("r-bio", "off", remove=True, log=lambda *_: None)
    assert view["reclaim"]["reclaimed"] is False
    assert any("live session" in (n or "") for n in notes)


_TESTS = [
    test_pack_env_id_resolves_local_env,
    test_pack_env_id_none_when_not_pack_backed,
    test_remove_artifacts_pack_backed_evicts_weft_env,
    test_remove_artifacts_blocked_surfaces_reason,
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
