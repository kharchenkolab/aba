"""
register_dataset adopts scratch-tier files into DATA_DIR.

The stumble it fixes: the agent downloads to a relative path (kernel cwd = the
thread/run SCRATCH dir), then register_dataset — which resolved only against
DATA_DIR — couldn't find the files and registered by-reference with a null path,
forcing a manual move + re-register. Now register_dataset:
  - resolves a bare path against the active Run / thread scratch dir too;
  - ADOPTS a scratch file into DATA_DIR (instant hardlink) so it persists past
    scratch GC, in ONE call — no move dance;
  - leaves a path already under DATA_DIR as-is.

Deterministic (no model). Run:
    .venv/bin/python tests/d17_register_adopt.py
"""
from __future__ import annotations
import os
import sys
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d17_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d17.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from core.graph.entities import create_entity, get_entity    # noqa: E402
from content.bio.lifecycle.runs import open_run               # noqa: E402
from content.bio.tools import register_dataset_tool           # noqa: E402
from core.data.workspace import scratch_dir                   # noqa: E402
from core import projects                                      # noqa: E402
from config import DATA_DIR                                    # noqa: E402
from core.config import WORK_DIR                                # noqa: E402,F401

_PID = projects.current() or "default"   # what run_python/_scratch_bases use

_failures = []


def check(label, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (f"  {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _within_data(p: str) -> bool:
    return os.path.abspath(p).startswith(os.path.abspath(str(DATA_DIR)) + os.sep)


TID = create_entity(entity_type="thread", title="t", metadata={"thread_id": None})
ctx = {"thread_id": TID}

print("adopt a relative-path download from the thread scratch dir")
thread_dir = scratch_dir(_PID, f"thread-{TID}")
d = Path(thread_dir) / "GSE_matrices"
(d / "GSM1").mkdir(parents=True)
(d / "GSM1" / "matrix.mtx.gz").write_bytes(b"counts")
(d / "GSM1" / "barcodes.tsv.gz").write_bytes(b"bc")
# Agent calls register with the BARE relative name (as it wrote it).
res = register_dataset_tool({"path": "GSE_matrices", "title": "GSE C1+C2"}, ctx)
check("registered ok in one call", res.get("status") == "ok", str(res))
ap = res.get("artifact_path")
check("artifact_path resolved (not null)", bool(ap), str(res))
check("adopted INTO DATA_DIR", ap and _tmp in ap and "/data/" in ap, str(ap))
check("note says adopted", "adopted" in (res.get("note") or "").lower(), str(res.get("note")))
ent = get_entity(res["dataset_id"])
check("by_reference cleared (now a kept copy)", (ent.get("metadata") or {}).get("by_reference") is False,
      str((ent.get("metadata") or {}).get("by_reference")))
check("adopted files present in DATA_DIR", (Path(ap) / "GSM1" / "matrix.mtx.gz").exists(), ap)

print("\nadopted copy survives scratch GC (hardlink keeps the data)")
shutil.rmtree(thread_dir, ignore_errors=True)   # simulate scratch cleanup
check("DATA_DIR copy still readable after scratch removed",
      (Path(ap) / "GSM1" / "matrix.mtx.gz").read_bytes() == b"counts", ap)

print("\nadopt from the active Run's output dir")
rid = open_run(TID, "a run")
run_dir = Path(get_entity(rid)["artifact_path"])
(run_dir / "out.h5ad").write_bytes(b"h5ad")
res2 = register_dataset_tool({"path": "out.h5ad", "title": "processed"}, ctx)
check("found in the run dir + adopted", res2.get("artifact_path") and _within_data(res2["artifact_path"]), str(res2))

print("\na path already under DATA_DIR is used as-is (no adopt)")
keep = Path(DATA_DIR) / "already.csv"
keep.write_text("a,b\n1,2\n")
res3 = register_dataset_tool({"path": "already.csv", "title": "kept"}, ctx)
check("uses DATA_DIR path directly", os.path.abspath(res3.get("artifact_path") or "") == str(keep.resolve()), str(res3))
check("not re-adopted (by_reference stays True)",
      (get_entity(res3["dataset_id"]).get("metadata") or {}).get("by_reference") is True, str(res3))

print("\nmissing path → by-reference with a warning (unchanged)")
res4 = register_dataset_tool({"path": "nope_missing", "title": "x"}, ctx)
check("null artifact_path + warning", res4.get("artifact_path") is None and "WARNING" in (res4.get("note") or ""),
      str(res4))

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
    sys.exit(1)
print("d17 OK")
