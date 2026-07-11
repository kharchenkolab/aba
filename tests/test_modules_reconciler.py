"""Modules Phase 2 — the backend reconciler (misc/modules.md).

Uses an injected runner so no real micromamba/subprocess runs; probe_ready is
monkeypatched to simulate the capability appearing (or not) after a script runs.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest                                       # noqa: E402
import core.modules.registry as reg                 # noqa: E402
import core.modules.state as st                     # noqa: E402
import core.modules.manager as mgr                  # noqa: E402
import core.modules.reconciler as rec               # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    rec._INFLIGHT.clear()
    rec._started = False
    yield


def _fake_runner(record, *, rc=0):
    def run(cmd, env, log_path):
        record.append((cmd[-1], env.get("ENV_DIR")))     # script path + a wired env var
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("fake log")
        return rc
    return run


def test_run_module_success_sets_ready(monkeypatch):
    monkeypatch.setattr(mgr, "probe_ready", lambda s: True)     # capability appears
    calls = []
    ok = rec.run_module(reg.get("python-bio"), runner=_fake_runner(calls), log=lambda *_: None)
    assert ok is True
    assert st.get_status("python-bio")["status"] == "idle"
    assert calls and calls[0][0].endswith("install-python-bio.sh")
    assert calls[0][1]                                          # ENV_DIR was passed


def test_run_module_nonzero_exit_fails(monkeypatch):
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    ok = rec.run_module(reg.get("r-bio"), runner=_fake_runner([], rc=1), log=lambda *_: None)
    assert ok is False
    assert st.get_status("r-bio")["status"] == "failed"
    assert "exited 1" in st.get_status("r-bio")["error"]


def test_run_module_zero_exit_but_not_detected_fails(monkeypatch):
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)    # script "succeeded" but no capability
    ok = rec.run_module(reg.get("viewer-pagoda3"), runner=_fake_runner([]), log=lambda *_: None)
    assert ok is False
    assert st.get_status("viewer-pagoda3")["status"] == "failed"


def test_run_module_missing_script_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(rec, "_repo_aba_root", lambda: tmp_path / "nope")
    ok = rec.run_module(reg.get("python-bio"), runner=_fake_runner([]), log=lambda *_: None)
    assert ok is False
    assert "missing" in st.get_status("python-bio")["error"]


def test_reconcile_runs_only_enabled_missing(monkeypatch):
    # python-bio on by default; r-bio + pagoda3 off. None ready yet.
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    calls = []
    res = rec.reconcile(runner=_fake_runner(calls), log=lambda *_: None)
    assert res["r-bio"] == "disabled" and res["viewer-pagoda3"] == "disabled"
    assert res["python-bio"] is False                          # ran, but probe still False
    assert [c[0].split("/")[-1] for c in calls] == ["install-python-bio.sh"]


def test_reconcile_skips_ready(monkeypatch):
    monkeypatch.setattr(mgr, "probe_ready", lambda s: s.id == "python-bio")
    calls = []
    res = rec.reconcile(runner=_fake_runner(calls), log=lambda *_: None)
    assert res["python-bio"] == "ready"
    assert calls == []                                         # nothing to install


def test_reconcile_installs_enabled_module(monkeypatch):
    st.set_desired("r-bio", "enabled")                         # user turned it on
    ready = {"r-bio": False}
    monkeypatch.setattr(mgr, "probe_ready", lambda s: ready.get(s.id, False))
    def runner(cmd, env, log_path):
        Path(log_path).parent.mkdir(parents=True, exist_ok=True); Path(log_path).write_text("x")
        if "install-r-bio.sh" in cmd[-1]:
            ready["r-bio"] = True                              # build makes it ready
        return 0
    res = rec.reconcile(runner=runner, log=lambda *_: None)
    assert res["r-bio"] is True
    assert st.get_status("r-bio")["status"] == "idle"


def test_ensure_module_persists_and_queues(monkeypatch):
    monkeypatch.setattr(rec, "run_module", lambda *a, **k: None)   # don't spawn a real build
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    v = rec.ensure_module("r-bio", log=lambda *_: None)
    assert v and v["id"] == "r-bio"
    assert st.get_desired("r-bio") == "enabled"                # intent persisted

    assert rec.ensure_module("does-not-exist") is None
