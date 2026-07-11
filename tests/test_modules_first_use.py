"""Modules Phase 5 — first-use gating (misc/modules.md)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest                                        # noqa: E402
import core.modules.manager as mgr                   # noqa: E402
import core.modules.reconciler as rec                # noqa: E402
import core.modules.first_use as fu                  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    rec._INFLIGHT.clear(); rec._started = False
    yield


def test_module_for_trigger_matches():
    assert fu.module_for_trigger("scanpy").id == "python-bio"
    assert fu.module_for_trigger("anndata").id == "python-bio"
    assert fu.module_for_trigger("pagoda3").id == "viewer-pagoda3"
    assert fu.module_for_trigger("some.file.lstar.zarr").id == "viewer-pagoda3"   # ext suffix
    assert fu.module_for_trigger("Seurat").id == "r-bio"                          # case-insensitive
    assert fu.module_for_trigger("numpy") is None                                # core, not a module


def test_ensure_for_trigger_ready_returns_none(monkeypatch):
    monkeypatch.setattr(mgr, "probe_ready", lambda s: True)                       # already installed
    assert fu.ensure_for_trigger("pagoda3") is None


def test_ensure_for_trigger_installs_and_notes(monkeypatch):
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    called = []
    monkeypatch.setattr(rec, "ensure_module", lambda mid, **k: called.append(mid))
    note = fu.ensure_for_trigger("pagoda3")
    assert note and note["module"] == "viewer-pagoda3" and note["ready"] is False
    assert "installing" in note["note"].lower()
    assert called == ["viewer-pagoda3"]                                          # install kicked


def test_ensure_for_trigger_unknown_returns_none():
    assert fu.ensure_for_trigger("totally-unknown") is None
    assert fu.ensure_for_trigger("") is None
