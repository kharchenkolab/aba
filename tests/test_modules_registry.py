"""Modules Phase 1 — registry, state, and read-only manager (misc/modules.md)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.modules.registry as reg          # noqa: E402
import core.modules.state as st              # noqa: E402
import core.modules.manager as mgr           # noqa: E402


# ── registry ────────────────────────────────────────────────────────────────
def test_registry_catalog_and_defaults():
    ids = reg.ids()
    assert set(ids) == {"python-bio", "r-bio", "viewer-pagoda3"}
    d = {m.id: m for m in reg.all_modules()}
    assert d["python-bio"].default_enabled is True          # on: wave at boot
    assert d["r-bio"].default_enabled is False              # off: first-use / manual
    assert d["viewer-pagoda3"].default_enabled is False
    assert d["python-bio"].removable is False               # base-update can't be reclaimed
    assert d["r-bio"].removable is True and d["viewer-pagoda3"].removable is True
    for m in reg.all_modules():                             # every module wires an install script
        assert m.install_script.startswith("install/core/modules/")


# ── state file ──────────────────────────────────────────────────────────────
def test_state_desired_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    assert st.get_desired("r-bio") is None                  # unset → default applies
    st.set_desired("r-bio", "enabled")
    assert st.get_desired("r-bio") == "enabled"
    st.set_desired("r-bio", "disabled")
    assert st.get_desired("r-bio") == "disabled"
    st.set_desired("r-bio", None)                           # clear
    assert st.get_desired("r-bio") is None


def test_state_status_roundtrip_and_clear(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    st.set_status("r-bio", "installing", progress="linking Seurat")
    got = st.get_status("r-bio")
    assert got["status"] == "installing" and got["progress"] == "linking Seurat"
    st.set_status("r-bio", "failed", error="solve failed")
    assert st.get_status("r-bio")["error"] == "solve failed"
    st.set_status("r-bio", "idle")                          # clean finish clears transient fields
    got = st.get_status("r-bio")
    assert got["status"] == "idle" and got["error"] is None and got["progress"] is None


def test_state_corrupt_file_reads_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    (tmp_path / "modules.json").write_text("{not json")
    assert st.load() == {"modules": {}}
    assert st.get_desired("python-bio") is None


# ── manager: enabled intent + live actual state ───────────────────────────────
def test_is_enabled_default_then_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    pybio = reg.get("python-bio")
    rbio = reg.get("r-bio")
    assert mgr.is_enabled(pybio) is True and mgr.is_enabled(rbio) is False   # defaults
    st.set_desired("r-bio", "enabled")
    st.set_desired("python-bio", "disabled")
    assert mgr.is_enabled(rbio) is True and mgr.is_enabled(pybio) is False   # overrides win


def test_actual_state_probe_wins_over_stale_status(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    rbio = reg.get("r-bio")
    # not present, no status → not_installed
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    assert mgr.actual_state(rbio) == "not_installed"
    # reconciler recorded installing → surfaced
    st.set_status("r-bio", "installing")
    assert mgr.actual_state(rbio) == "installing"
    # probe now says ready → ready wins even though status still 'installing'
    monkeypatch.setattr(mgr, "probe_ready", lambda s: True)
    assert mgr.actual_state(rbio) == "ready"


def test_probe_python_bio_tracks_base_stage(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    import core.exec.env_integrity as ei
    monkeypatch.setattr(ei, "base_stage", lambda: "boot")
    assert mgr.probe_ready(reg.get("python-bio")) is False
    monkeypatch.setattr(ei, "base_stage", lambda: "ready")
    assert mgr.probe_ready(reg.get("python-bio")) is True


def test_probe_pagoda3_needs_dist_and_reader(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setattr(mgr, "_base_env", lambda: tmp_path / "env")
    v = reg.get("viewer-pagoda3")
    assert mgr.probe_ready(v) is False
    # dist present but reader missing → still not ready (Phase 6: reader is a module dep)
    dist = tmp_path / "vendor" / "pagoda3" / "dist"; dist.mkdir(parents=True)
    (dist / "index.html").write_text("<html>")
    assert mgr.probe_ready(v) is False
    # add the lstar reader in the base env → ready
    sp = tmp_path / "env" / "lib" / "python3.12" / "site-packages" / "lstar"
    sp.mkdir(parents=True)
    assert mgr.probe_ready(v) is True


def test_module_view_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    views = mgr.list_modules()
    assert {v["id"] for v in views} == {"python-bio", "r-bio", "viewer-pagoda3"}
    v = next(v for v in views if v["id"] == "viewer-pagoda3")
    for k in ("title", "description", "size", "est_time", "enabled", "actual",
              "on_disk", "removable", "first_use"):
        assert k in v
    assert v["enabled"] is False and v["actual"] == "not_installed"
