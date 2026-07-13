"""Modules Phase 2 (misc/modules.md): declarative readiness probes + remove are
interpreted generically from the manifest — no per-module Python.
"""
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.modules.manager as mgr             # noqa: E402
import core.modules.reconciler as rec          # noqa: E402
import core.modules.registry as reg            # noqa: E402


def _spec(ready=None, remove=None):
    base = reg.get("r-bio")
    return replace(base, ready=ready or {}, remove=remove or {})


def test_probe_base_stage(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    import core.exec.env_integrity as ei
    monkeypatch.setattr(ei, "base_stage", lambda: "boot")
    assert mgr.probe_ready(_spec(ready={"base_stage": "ready"})) is False
    monkeypatch.setattr(ei, "base_stage", lambda: "ready")
    assert mgr.probe_ready(_spec(ready={"base_stage": "ready"})) is True


def test_probe_path_exists_expands_vars(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    s = _spec(ready={"path_exists": "$PAGODA3_DIST/index.html"})
    assert mgr.probe_ready(s) is False
    d = tmp_path / "vendor" / "pagoda3" / "dist"; d.mkdir(parents=True)
    (d / "index.html").write_text("<html>")
    assert mgr.probe_ready(s) is True


def test_probe_r_package(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("ABA_RUNTIME_DIR", str(tmp_path / "runtime"))
    s = _spec(ready={"r_package": {"env": "tools", "package": "Seurat"}})
    assert mgr.probe_ready(s) is False
    t = tmp_path / "runtime" / "envs" / "tools"
    (t / "bin").mkdir(parents=True); (t / "bin" / "Rscript").write_text("#!/bin/sh\n")
    assert mgr.probe_ready(s) is False                    # Rscript but no Seurat
    (t / "lib" / "R" / "library" / "Seurat").mkdir(parents=True)
    assert mgr.probe_ready(s) is True


def test_probe_python_import(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setattr(mgr, "_base_env", lambda: tmp_path / "env")
    s = _spec(ready={"python_import": "scanpy"})
    assert mgr.probe_ready(s) is False
    (tmp_path / "env" / "lib" / "python3.12" / "site-packages" / "scanpy").mkdir(parents=True)
    assert mgr.probe_ready(s) is True


def test_probe_unknown_or_empty_is_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    assert mgr.probe_ready(_spec(ready={})) is False
    assert mgr.probe_ready(_spec(ready={"bogus_kind": 1})) is False


def test_remove_paths_expands_and_deletes(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("ABA_RUNTIME_DIR", str(tmp_path / "runtime"))
    t = tmp_path / "runtime" / "envs" / "tools"; t.mkdir(parents=True)
    (t / "marker").write_text("x")
    logs = []
    rec._remove_artifacts(_spec(remove={"paths": ["$TOOLS_ENV"]}), logs.append)
    assert not t.exists() and any("removed" in m for m in logs)
