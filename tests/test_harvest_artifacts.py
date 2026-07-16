"""Regression tests for harvest_artifacts off-convention figure capture (C2).

The live bug (forensic on msa_phylo): the agent `savefig`'d straight into the store
dir (`/artifacts/<pid>/cytc_tree.png`) instead of the scratch cwd; harvest scanned
only scratch, so 5 figures were on disk but `produced=[]` — orphaned/unpinnable.
Fix: harvest also registers files the agent wrote INTO the store during this exec
(mtime in [since_ts, harvest-begin), excluding our own copies) + nudges the agent.

Timing note: the harvest window is mtime-based, and some cluster filesystems (beegfs)
record mtime at **1-second granularity**. So we never lean on sub-second sleeps to
separate "before" from "during" the window — we stamp each file's mtime explicitly
with os.utime, which is deterministic regardless of FS granularity.

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


def _mkpng(p: Path, mtime: float | None = None):
    """A non-blank PNG (random noise → wide range → passes the blank check).

    If ``mtime`` is given, stamp it explicitly — the harvest window compares file
    mtimes against ``since_ts``, and on a coarse-mtime FS a wall-clock ``time.time()``
    can sit *after* a file's floored mtime, spuriously dropping it. Stamping makes the
    before/during/after relation exact."""
    import numpy as np
    from PIL import Image
    a = np.random.default_rng(0).integers(0, 255, size=(48, 48, 3), dtype="uint8")
    Image.fromarray(a).save(p)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


# A fixed reference well in the past; harvest's upper bound is time.time() (now),
# so files stamped at START+N (N << 100) land inside the [since_ts, now) window.
START = time.time() - 100.0


def _isolate(tmp_path, monkeypatch):
    adir = tmp_path / "store"; adir.mkdir()
    scratch = tmp_path / "scratch"; scratch.mkdir()
    import core.config as cfg
    import core.projects as proj      # current_project_id lives here (config burn-down #1)
    monkeypatch.setattr(proj, "current_project_id", lambda: "test")
    monkeypatch.setattr(cfg, "project_artifacts_dir", lambda pid: adir)
    return adir, scratch


def test_scratch_figure_still_harvested(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    _mkpng(scratch / "umap.png", mtime=START + 50)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
    assert {p["original_name"] for p in plots} == {"umap.png"}
    assert not warns


def test_offconvention_store_write_registered_and_warned(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    _mkpng(scratch / "umap.png", mtime=START + 50)        # normal (scratch)
    _mkpng(adir / "cytc_tree.png", mtime=START + 50)      # OFF-CONVENTION: agent wrote into the store
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
    names = {p["original_name"] for p in plots}
    assert "umap.png" in names                # regression
    assert "cytc_tree.png" in names           # A: off-convention figure now registered
    assert len(plots) == 2                    # no double-count
    assert any("artifacts dir" in w for w in warns)   # B: nudge emitted


def test_preexisting_store_figure_not_recaught(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    _mkpng(adir / "old.png", mtime=START - 50)            # written BEFORE the exec window
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
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
    import core.projects as proj      # current_project_id lives here (config burn-down #1)
    monkeypatch.setattr(proj, "current_project_id", lambda: "test")
    monkeypatch.setattr(cfg, "project_artifacts_dir", lambda pid: adir)
    monkeypatch.setattr(cfg, "project_work_dir", lambda pid: work)
    return adir, work, scratch


def test_workdir_figure_registered_and_warned(tmp_path, monkeypatch):
    adir, work, scratch = _isolate_with_work(tmp_path, monkeypatch)
    _mkpng(scratch / "umap.png", mtime=START + 50)            # normal (in the thread cwd)
    _mkpng(work / "gfp_presentation.png", mtime=START + 50)   # OFF-CONVENTION: absolute path to work/ (parent)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
    names = {p["original_name"] for p in plots}
    assert "umap.png" in names                # regression (thread cwd still works)
    assert "gfp_presentation.png" in names    # C4: work-dir figure now captured
    assert len(plots) == 2                    # no double-count
    assert any("work dir" in w for w in warns)   # nudge emitted


def test_workdir_preexisting_not_recaught(tmp_path, monkeypatch):
    adir, work, scratch = _isolate_with_work(tmp_path, monkeypatch)
    _mkpng(work / "old_wd.png", mtime=START - 50)            # written BEFORE the exec window
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
    assert "old_wd.png" not in {p["original_name"] for p in plots}


# --- A0: oversize files are recorded link-only, not silently dropped ---
# The crown-jewel bug: a >50 MB output (processed dataset, model) was warned about
# and DROPPED from produced[] — so nothing could retain it and it aged out of the
# sandbox. Now it lands in `files` as a link-only entry (no served url) → a retain
# candidate + visible in the Files tab. (misc/output_durability.md §9 A0.)

def _mklarge(p: Path, size: int, mtime: float | None = None):
    with open(p, "wb") as fh:
        fh.truncate(size)          # sparse: apparent size == `size`, no real bytes
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def test_large_file_recorded_link_only_not_dropped(tmp_path, monkeypatch):
    adir, scratch = _isolate(tmp_path, monkeypatch)
    big = scratch / "model.rds"
    size = runmod._MAX_HARVEST_BYTES + 1024 * 1024      # just over the cap
    _mklarge(big, size, mtime=START + 50)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
    entry = next((f for f in files if f.get("original_name") == "model.rds"), None)
    assert entry is not None, f"large file dropped, not recorded: {files}"
    assert entry.get("link_only") is True
    assert entry.get("url") is None                     # not inline-linkable
    assert entry.get("bytes") == size
    assert not list(adir.glob("*.rds"))                 # NOT copied into the store
    assert any("too large" in w and "retained" in w for w in warns)   # honest warning


def test_small_file_still_copied_with_url(tmp_path, monkeypatch):
    """Guard the normal path: an under-cap file is copied and served as before."""
    adir, scratch = _isolate(tmp_path, monkeypatch)
    small = scratch / "result.rds"
    _mklarge(small, 1024, mtime=START + 50)
    plots, tables, files, warns = runmod.harvest_artifacts(scratch, since_ts=START)
    entry = next((f for f in files if f.get("original_name") == "result.rds"), None)
    assert entry is not None and entry.get("url"), f"small file not copied: {files}"
    assert not entry.get("link_only")
    assert list(adir.glob("*.rds"))                     # copied into the store


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
