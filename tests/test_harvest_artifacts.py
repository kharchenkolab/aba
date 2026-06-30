"""Regression tests for harvest_artifacts off-convention figure capture (C2).

The live bug (forensic on msa_phylo): the agent `savefig`'d straight into the store
dir (`/artifacts/<pid>/cytc_tree.png`) instead of the scratch cwd; harvest scanned
only scratch, so 5 figures were on disk but `produced=[]` — orphaned/unpinnable.
Fix: harvest also registers files the agent wrote INTO the store during this exec
(mtime in [since_ts, harvest-begin), excluding our own copies) + nudges the agent.

Run: .venv/bin/python -m pytest tests/test_harvest_artifacts.py -q
"""
from __future__ import annotations
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_harvest_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "h.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
sys.path.insert(0, str(ROOT / "backend"))

from core.exec import run as runmod  # noqa: E402


def _mkpng(p: Path):
    """A non-blank PNG (random noise → wide range → passes the blank check)."""
    import numpy as np
    from PIL import Image
    a = np.random.default_rng(0).integers(0, 255, size=(48, 48, 3), dtype="uint8")
    Image.fromarray(a).save(p)


def _isolate(tmp_path, monkeypatch):
    adir = tmp_path / "store"; adir.mkdir()
    scratch = tmp_path / "scratch"; scratch.mkdir()
    import core.config as cfg
    monkeypatch.setattr(cfg, "current_project_id", lambda: "test")
    monkeypatch.setattr(cfg, "project_artifacts_dir", lambda pid: adir)
    return adir, scratch


def test_scratch_figure_still_harvested(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    start = time.time(); time.sleep(0.05)
    _mkpng(scratch / "umap.png")
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=start)
    assert {p["original_name"] for p in plots} == {"umap.png"}
    assert not warns


def test_offconvention_store_write_registered_and_warned(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    start = time.time(); time.sleep(0.05)
    _mkpng(scratch / "umap.png")              # normal (scratch)
    _mkpng(adir / "cytc_tree.png")            # OFF-CONVENTION: agent wrote into the store
    time.sleep(0.05)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=start)
    names = {p["original_name"] for p in plots}
    assert "umap.png" in names                # regression
    assert "cytc_tree.png" in names           # A: off-convention figure now registered
    assert len(plots) == 2                    # no double-count
    assert any("artifacts dir" in w for w in warns)   # B: nudge emitted


def test_preexisting_store_figure_not_recaught(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    _mkpng(adir / "old.png")                  # written BEFORE the exec window
    time.sleep(0.05); start = time.time(); time.sleep(0.05)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=start)
    assert "old.png" not in {p["original_name"] for p in plots}
    assert plots == []


def test_offconvention_skipped_when_no_since_ts(tmp_path, monkeypatch):
    """One-shot path (since_ts=0): off-convention pass is gated off to avoid over-catching."""
    adir, scratch = _isolate(tmp_path, monkeypatch)
    _mkpng(adir / "stray.png")
    plots, tables, files, warns = runmod.harvest_artifacts(scratch)  # since_ts=0.0
    assert "stray.png" not in {p["original_name"] for p in plots}


# --- C4: figures saved to the PROJECT WORK DIR (parent of the per-thread exec cwd) ---
# The "A2" apparent-fabrication: the agent savefig'd an absolute path into the project
# work dir (parent of cwd); the cell returned rc=0 + its own "figure saved" print, but
# harvest scanned only the thread cwd → produced=[] → looked like the agent lied.

def _isolate_with_work(tmp_path, monkeypatch):
    adir = tmp_path / "store"; adir.mkdir()
    work = tmp_path / "work"; work.mkdir()
    scratch = work / "thread-x"; scratch.mkdir()    # the per-thread exec cwd
    import core.config as cfg
    monkeypatch.setattr(cfg, "current_project_id", lambda: "test")
    monkeypatch.setattr(cfg, "project_artifacts_dir", lambda pid: adir)
    monkeypatch.setattr(cfg, "project_work_dir", lambda pid: work)
    return adir, work, scratch


def test_workdir_figure_registered_and_warned(tmp_path, monkeypatch):
    adir, work, scratch = _isolate_with_work(tmp_path, monkeypatch)
    start = time.time(); time.sleep(0.05)
    _mkpng(scratch / "umap.png")              # normal (in the thread cwd)
    _mkpng(work / "gfp_presentation.png")     # OFF-CONVENTION: absolute path to work/ (parent)
    time.sleep(0.05)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=start)
    names = {p["original_name"] for p in plots}
    assert "umap.png" in names                # regression (thread cwd still works)
    assert "gfp_presentation.png" in names    # C4: work-dir figure now captured
    assert len(plots) == 2                    # no double-count
    assert any("work dir" in w for w in warns)   # nudge emitted


def test_workdir_preexisting_not_recaught(tmp_path, monkeypatch):
    adir, work, scratch = _isolate_with_work(tmp_path, monkeypatch)
    _mkpng(work / "old_wd.png")               # written BEFORE the exec window
    time.sleep(0.05); start = time.time(); time.sleep(0.05)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=start)
    assert "old_wd.png" not in {p["original_name"] for p in plots}


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
