"""Modules Phase 3 — /api/modules CRUD + progress (misc/modules.md).

Calls the route functions directly (they take no project dependency). run_module is
stubbed so enable/retry don't spawn a real install.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest                                        # noqa: E402
from fastapi import HTTPException                    # noqa: E402
import core.modules.state as st                      # noqa: E402
import core.modules.manager as mgr                   # noqa: E402
import core.modules.reconciler as rec                # noqa: E402
from core.web.routers import modules as api          # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    rec._INFLIGHT.clear(); rec._started = False
    monkeypatch.setattr(rec, "run_module", lambda *a, **k: None)   # never a real install
    yield


def test_list_modules_shape():
    out = api.list_modules()
    ids = {m["id"] for m in out["modules"]}
    assert ids == {"python-bio", "r-bio", "viewer-pagoda3"}


def test_enable_persists_and_returns_view():
    v = api.enable_module("r-bio")
    assert v["id"] == "r-bio" and st.get_desired("r-bio") == "on" and v["mode"] == "on"


def test_set_mode_first_use():
    v = api.set_module_mode("r-bio", "first_use")
    assert v["mode"] == "first_use" and st.get_desired("r-bio") == "first_use"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        api.set_module_mode("r-bio", "bogus")
    assert ei.value.status_code == 400


def test_enable_unknown_404():
    with pytest.raises(HTTPException) as ei:
        api.enable_module("nope")
    assert ei.value.status_code == 404


def test_disable_keeps_on_disk_by_default(monkeypatch):
    st.set_desired("r-bio", "on")
    v = api.disable_module("r-bio", remove=False)
    assert st.get_desired("r-bio") == "off" and v["mode"] == "off"


def test_disable_remove_nonremovable_400():
    with pytest.raises(HTTPException) as ei:
        api.disable_module("python-bio", remove=True)     # base-resident → not removable
    assert ei.value.status_code == 400


def test_disable_remove_deletes_artifacts(monkeypatch, tmp_path):
    # lay down a fake pagoda3 dist, then reclaim it (reader is core → not touched)
    dist = tmp_path / "vendor" / "pagoda3" / "dist"
    dist.mkdir(parents=True); (dist / "index.html").write_text("<html>")
    assert mgr.probe_ready(mgr.registry.get("viewer-pagoda3")) is True
    api.disable_module("viewer-pagoda3", remove=True)
    assert not dist.exists()
    assert mgr.probe_ready(mgr.registry.get("viewer-pagoda3")) is False


def test_retry_reinstalls_without_changing_mode():
    st.set_status("r-bio", "failed", error="boom")
    v = api.retry_module("r-bio")                              # r-bio default first_use → allowed
    assert v["id"] == "r-bio" and v["mode"] == "first_use"    # mode unchanged
    assert st.get_status("r-bio")["status"] == "queued"


def test_module_log_tail(monkeypatch, tmp_path):
    logp = tmp_path / "logs" / "module-python-bio.log"
    logp.parent.mkdir(parents=True)
    logp.write_text("\n".join(f"line{i}" for i in range(10)))
    out = api.module_log("python-bio", tail=3)
    assert out["lines"] == ["line7", "line8", "line9"]
    assert api.module_log("python-bio", tail=200)["lines"][0] == "line0"

    with pytest.raises(HTTPException):
        api.module_log("nope")
