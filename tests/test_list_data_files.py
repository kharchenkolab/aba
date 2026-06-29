"""Regression tests for list_data_files (content/bio/tools/file_io.py).

Guards the fix for the live failure where an agent concluded "no data — ask the
user to upload" while data sat on disk: list_data_files used to scan only the
TOP LEVEL of DATA_DIR and gate on an extension allowlist, so a folder-of-files
dataset (e.g. an image set coloc/) and imaging files (.tif/.nii) were invisible.
The fix recurses + drops the allowlist. Diagnosed via the forensic agent on the
colocalization scenario (misc/scenarios/_runs/colocalization-*); confirmed by the
colocalization scenario going 1/12 -> 11/12 after the fix.

Run: .venv/bin/python -m pytest tests/test_list_data_files.py -q
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_ldf_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "ldf.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
sys.path.insert(0, str(ROOT / "backend"))

from content.bio.tools import file_io  # noqa: E402


def _isolate(tmp_path, monkeypatch):
    """Point list_data_files at an empty registry + a tmp DATA_DIR (no DB)."""
    dd = tmp_path / "data"; dd.mkdir()
    monkeypatch.setattr(file_io, "_registered_datasets", lambda: [])
    import core.config as cfg
    monkeypatch.setattr(cfg, "project_data_dir", lambda pid: dd)
    monkeypatch.setattr(cfg, "current_project_id", lambda: "test")
    return dd


def test_subdir_files_surface(tmp_path, monkeypatch):
    """Files in a SUBDIRECTORY must appear with their relative path (the coloc/ bug)."""
    dd = _isolate(tmp_path, monkeypatch)
    (dd / "coloc").mkdir()
    (dd / "coloc" / "f1_ch1.tif").write_bytes(b"II*\x00x")
    (dd / "coloc" / "f1_ch2.tif").write_bytes(b"II*\x00x")
    (dd / "flat.csv").write_text("a,b\n1,2\n")
    names = {f["filename"] for f in file_io.list_data_files({})["files"]}
    assert "coloc/f1_ch1.tif" in names
    assert "coloc/f1_ch2.tif" in names
    assert "flat.csv" in names


def test_imaging_and_genomics_extensions_listed(tmp_path, monkeypatch):
    """No extension allowlist — imaging/genomics files must not be silently hidden."""
    dd = _isolate(tmp_path, monkeypatch)
    for n in ("a.tif", "b.tiff", "c.png", "d.nii", "e.vcf", "f.bed", "g.json"):
        (dd / n).write_bytes(b"x")
    names = {f["filename"] for f in file_io.list_data_files({})["files"]}
    assert {"a.tif", "b.tiff", "c.png", "d.nii", "e.vcf", "f.bed", "g.json"} <= names


def test_no_datasets_message_only_when_truly_empty(tmp_path, monkeypatch):
    """The 'ask the user to upload' message must fire ONLY when nothing is on disk
    (recursively) — the misleading-emptiness bug."""
    dd = _isolate(tmp_path, monkeypatch)
    out = file_io.list_data_files({})
    assert out["files"] == [] and "no datasets" in out["message"].lower()
    (dd / "sub").mkdir(); (dd / "sub" / "x.tif").write_bytes(b"x")
    out2 = file_io.list_data_files({})
    assert out2["files"], "subdir file should make the project non-empty"
    assert "no datasets" not in out2.get("message", "").lower()


def test_dotfiles_and_junk_skipped(tmp_path, monkeypatch):
    dd = _isolate(tmp_path, monkeypatch)
    (dd / "real.csv").write_text("x\n")
    (dd / ".hidden").write_text("x")
    (dd / "junk.pyc").write_bytes(b"x")
    names = {f["filename"] for f in file_io.list_data_files({})["files"]}
    assert "real.csv" in names
    assert ".hidden" not in names and "junk.pyc" not in names


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
