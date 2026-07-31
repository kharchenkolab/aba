"""Harvest store identity: derived from CONTENT, never minted by the copy.

The class this guards against: a transport layer (the sandbox→store harvest
copy) inventing the artifact's identity. The store name must be the file's
sha256 (truncated), so:
  - same bytes → same store name (dedup: ONE copy, however many harvests);
  - different bytes → different names (no clobber);
  - produced[] carries a real `sha256` (the field core.exec.artifacts always
    read but nothing ever wrote);
  - a second run producing identical bytes is idempotent on the store.
Smell test made executable: if the same input yields a different identifier on
a second run, it's allocation, not addressing.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_harvest_id_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_tmp, "t.db"))

from core.exec.run import harvest_artifacts  # noqa: E402

pytestmark = pytest.mark.platform

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8   # non-blank-ish payload


def _harvest(tmp_path, files: dict, pid="prjH"):
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        p = scratch / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    plots, tables, out_files, warns = harvest_artifacts(scratch, since_ts=0,
                                                        project_id=pid)
    return plots, tables, out_files, warns


def test_same_bytes_same_store_name_across_harvests(tmp_path, monkeypatch):
    _, t1, _, _ = _harvest(tmp_path / "a", {"counts.csv": b"a,b\n1,2\n"})
    _, t2, _, _ = _harvest(tmp_path / "b", {"renamed.csv": b"a,b\n1,2\n"})
    assert t1 and t2
    n1 = t1[0]["url"].rsplit("/", 1)[-1]
    n2 = t2[0]["url"].rsplit("/", 1)[-1]
    assert n1 == n2, "identical bytes must map to ONE store identity"
    assert t1[0]["sha256"] == t2[0]["sha256"]
    # the two manifest entries keep their own names — identity ≠ display name
    assert t1[0]["original_name"] == "counts.csv"
    assert t2[0]["original_name"] == "renamed.csv"


def test_different_bytes_different_store_names(tmp_path):
    _, tables, _, _ = _harvest(tmp_path, {"x.csv": b"1\n", "y.csv": b"2\n"})
    names = {t["url"].rsplit("/", 1)[-1] for t in tables}
    assert len(names) == 2


def test_sha256_recorded_and_true(tmp_path):
    import hashlib
    data = b"c1,c2\n3,4\n"
    _, tables, _, _ = _harvest(tmp_path, {"t.csv": data})
    assert tables[0]["sha256"] == hashlib.sha256(data).hexdigest()
    served = tables[0]["url"].rsplit("/", 1)[-1]
    assert served == tables[0]["sha256"][:32] + ".csv"


def test_rerun_is_idempotent_on_the_store(tmp_path):
    from core.config import project_artifacts_dir
    _harvest(tmp_path / "r1", {"out.csv": b"same\n"}, pid="prjI")
    adir = Path(project_artifacts_dir("prjI"))
    first = {p.name: p.stat().st_ino for p in adir.iterdir()}
    _harvest(tmp_path / "r2", {"out.csv": b"same\n"}, pid="prjI")
    second = {p.name: p.stat().st_ino for p in adir.iterdir()}
    assert first == second, "re-harvesting identical bytes must not grow the store"


def test_store_copy_hardlinks_when_possible(tmp_path):
    from core.config import project_artifacts_dir
    scratch = tmp_path / "s"
    scratch.mkdir()
    src = scratch / "big.csv"
    src.write_bytes(b"payload\n")
    harvest_artifacts(scratch, since_ts=0, project_id="prjL")
    adir = Path(project_artifacts_dir("prjL"))
    stored = next(adir.iterdir())
    if os.stat(src).st_dev == os.stat(stored).st_dev:
        assert os.stat(stored).st_nlink >= 2, \
            "same-device store copy should hardlink, not duplicate bytes"
