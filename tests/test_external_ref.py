"""External-reference drift baseline + detection (misc/external_import.md).

The by-reference import path stores a compact stat-only fingerprint of the external payload so a
later re-walk can FLAG (never re-copy) when it changes or vanishes. These are the load-bearing
unit tests for that helper; the recovery/import integration tests live in p16_external_import.py.

Run: .venv/bin/python -m pytest tests/test_external_ref.py -q
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.data.external_ref import fingerprint, check_drift, resolve_external  # noqa: E402


def _touch(p: Path, content: str = "x", mtime: int | None = None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    if mtime is not None:
        os.utime(p, (mtime, mtime))


def test_fingerprint_dir_and_file(tmp_path):
    assert fingerprint(str(tmp_path / "nope")) == {"exists": False}
    d = tmp_path / "results"
    _touch(d / "a.txt", "hello", mtime=1000)
    _touch(d / "sub" / "b.csv", "1,2,3", mtime=2000)
    fp = fingerprint(str(d))
    assert fp["exists"] and fp["n_files"] == 2
    assert fp["total_bytes"] == len("hello") + len("1,2,3")
    assert fp["max_mtime"] == 2000
    assert fp["digest"] and not fp["truncated"]
    assert fingerprint(str(d))["digest"] == fp["digest"]           # stable across walks
    fpf = fingerprint(str(d / "a.txt"))
    assert fpf["exists"] and fpf["n_files"] == 1                    # single file


def test_check_drift_fresh_changed_missing(tmp_path):
    d = tmp_path / "run1"
    _touch(d / "report.html", "<html>", mtime=1000)
    base = fingerprint(str(d))
    md = {"ref_path": str(d), "import_fingerprint": base}
    assert check_drift(md) == {"stale": False}                     # unchanged → fresh
    assert check_drift({})["stale"] is False                       # not an external entity
    assert check_drift({"ref_path": str(d)})["stale"] is False     # no baseline → can't say stale

    _touch(d / "extra.txt", "new", mtime=3000)                     # a file appears
    drift = check_drift(md)
    assert drift["stale"] and drift["reason"] == "changed", drift

    import shutil
    shutil.rmtree(d)                                               # the whole tree vanishes
    dm = check_drift(md)
    assert dm["stale"] and dm["reason"] == "missing", dm


def test_change_signals_resize_and_delete(tmp_path):
    d = tmp_path / "r"
    _touch(d / "f.txt", "aaa", mtime=1000)
    _touch(d / "g.txt", "bbb", mtime=1000)
    base = fingerprint(str(d))
    md = {"ref_path": str(d), "import_fingerprint": base}
    # same name + same mtime but different SIZE (a re-run that rewrote a file in place)
    _touch(d / "f.txt", "aaaaaa", mtime=1000)
    assert check_drift(md)["reason"] == "changed"
    # restore size, then delete a file → still detected
    _touch(d / "f.txt", "aaa", mtime=1000)
    assert check_drift(md)["stale"] is False                       # back to baseline
    (d / "g.txt").unlink()
    assert check_drift(md)["reason"] == "changed"


def test_resolve_external(tmp_path):
    d = tmp_path / "x"; d.mkdir()
    ap, exists = resolve_external(str(d))
    assert ap == str(d) and exists
    ap2, exists2 = resolve_external(str(tmp_path / "missing"))
    assert not exists2 and os.path.isabs(ap2)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
