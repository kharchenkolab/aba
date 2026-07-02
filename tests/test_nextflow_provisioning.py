"""Custom shared-FS Nextflow provisioning (misc/nfcore.md §7d, slim-SIF deploy).

ABA_NEXTFLOW_BIN (self-installed NF on shared FS) + ABA_NEXTFLOW_HOME (persistent NXF_HOME) are an
ALTERNATIVE to the cluster module. The invariant: when BIN is unset, the module path is unchanged —
so fat SIF and personal installs keep working exactly as before.
"""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core.exec import nextflow as nf  # noqa: E402


def test_nextflow_bin_dir_resolves(tmp_path):
    assert nf.nextflow_bin_dir(None) is None
    assert nf.nextflow_bin_dir("") is None
    d = tmp_path / "opt" / "nextflow"; d.mkdir(parents=True)
    assert nf.nextflow_bin_dir(str(d)) == str(d)               # a directory → itself
    launcher = d / "nextflow"; launcher.write_text("#!/bin/sh\n")
    assert nf.nextflow_bin_dir(str(launcher)) == str(d)        # a launcher file → its dir


def test_config_bin_home_knobs(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_NEXTFLOW_BIN", str(tmp_path / "opt" / "nextflow"))
    monkeypatch.setenv("ABA_NEXTFLOW_HOME", str(tmp_path / "nxfhome"))
    cfg = nf.nextflow_config()
    assert cfg["bin"] == str(tmp_path / "opt" / "nextflow")
    assert cfg["home"] == str(tmp_path / "nxfhome")


def test_module_path_unaffected_when_bin_unset(monkeypatch):
    # fat SIF / personal install: module set, NO bin → module honored, bin/home None (non-breaking).
    monkeypatch.delenv("ABA_NEXTFLOW_BIN", raising=False)
    monkeypatch.delenv("ABA_NEXTFLOW_HOME", raising=False)
    monkeypatch.setenv("ABA_NEXTFLOW_MODULE", "nextflow/24.10.6")
    cfg = nf.nextflow_config()
    assert cfg["module"] == "nextflow/24.10.6"
    assert cfg["bin"] is None and cfg["home"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
