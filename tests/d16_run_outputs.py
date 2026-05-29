"""
Run output directory — a Run owns a browsable output dir (entity-model v3).

When a Run is open, run_python/run_r write into WORK_DIR/<project>/<run_id>/, and:
  - that dir is recorded as the Run's artifact_path;
  - the Files tree shows the FULL nested dir under threads/<t>/runs/<r>/output/
    (.rds/.h5ad/subfolders, not just harvested png/csv);
  - the working/ catch-all does NOT duplicate those files;
  - refresh_output_manifest lists every file (figure/table/file) for the Run view;
  - GET /api/runs/{id}/file serves a file and rejects path traversal.

Deterministic (no model). Run:
    .venv/bin/python tests/d16_run_outputs.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d16_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d16.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from core.graph.entities import get_entity                   # noqa: E402
from content.bio.lifecycle.runs import open_run, refresh_output_manifest  # noqa: E402
from content.bio.lifecycle.registry import register_artifacts_from_tool_result  # noqa: E402
from content.bio.files.tree import build_files_tree          # noqa: E402
from core.graph.entities import create_entity                # noqa: E402

# Runs render under their home thread, so materialize one (as the real flow does).
TID = create_entity(entity_type="thread", title="d16 thread", metadata={"thread_id": None})
_failures = []


def check(label, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (f"  {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _walk(node, pred, acc):
    if pred(node):
        acc.append(node)
    for c in node.get("children") or []:
        _walk(c, pred, acc)
    return acc


print("a Run owns an output directory")
rid = open_run(TID, "pagoda2 clustering")
run = get_entity(rid)
out_dir = run.get("artifact_path")
check("open_run records an output dir (artifact_path)", bool(out_dir), str(run))
check("dir is keyed by the run id", out_dir and rid in out_dir, str(out_dir))

# Simulate a pipeline writing a mix of files into the run dir, with subfolders.
base = Path(out_dir)
(base / "umap.png").write_bytes(b"\x89PNG\r\n\x1a\n fake")
(base / "markers.csv").write_text("gene,cluster\nCD3D,1\n")
(base / "p2object.rds").write_bytes(b"rds-bytes")
(base / "model").mkdir(exist_ok=True)
(base / "model" / "weights.bin").write_bytes(b"weights")

# A cell's tool result (png harvested → figure entity; the rest only on disk).
res = {"plots": [{"url": "/artifacts/abc.png", "original_name": "umap.png"}],
       "execution_mode": "session"}
register_artifacts_from_tool_result(
    tool_name="run_python", tool_input={"code": "# pipeline"}, result_obj=res,
    focused_entity_id=None, analysis_ctx={"analysis_id": rid}, thread_id=TID)

print("\nthe Files tree shows the full output dir under the Run")
tree = build_files_tree()
out_nodes = _walk(tree, lambda n: n.get("kind") == "folder" and n.get("path", "").endswith("/output"), [])
check("runs/<run>/output/ folder exists", len(out_nodes) == 1, str([n.get("path") for n in out_nodes]))
files = _walk(out_nodes[0], lambda n: n.get("kind") == "file", []) if out_nodes else []
names = {f["name"] for f in files}
check("output/ lists the .rds", "p2object.rds" in names, str(names))
check("output/ lists the nested model file", "weights.bin" in names, str(names))
check("output/ lists figure + table files too", {"umap.png", "markers.csv"} <= names, str(names))
check("nested subfolder preserved", any("/output/model/" in f["path"] for f in files), str([f["path"] for f in files]))

print("\nworking/ does NOT duplicate the Run's files")
work_nodes = _walk(tree, lambda n: n.get("kind") == "folder" and n.get("path") == "working", [])
if work_nodes:
    wfiles = {f["name"] for f in _walk(work_nodes[0], lambda n: n.get("kind") == "file", [])}
    check("run files absent from working/", not ({"p2object.rds", "weights.bin"} & wfiles), str(wfiles))
else:
    check("run files absent from working/ (no working node at all)", True)

print("\nthe Run-view manifest lists every output, classified")
run = get_entity(rid)
manifest = (run.get("metadata") or {}).get("run") or {}
outs = manifest.get("outputs") or []
bykind = {}
for o in outs:
    bykind.setdefault(o["kind"], set()).add(o["label"])
check("manifest has a figure (umap.png)", "umap.png" in bykind.get("figure", set()), str(bykind))
check("figure thumb uses the harvested /artifacts url", any(
    o["kind"] == "figure" and (o.get("thumb") or "").startswith("/artifacts/") for o in outs), str(outs))
check("manifest has a table (markers.csv)", "markers.csv" in bykind.get("table", set()), str(bykind))
check("manifest has the .rds as a file", "p2object.rds" in bykind.get("file", set()), str(bykind))
check("file rows link to the run-file endpoint", any(
    o["kind"] == "file" and f"/api/runs/{rid}/file" in (o.get("href") or "") for o in outs), str(outs))

print("\nGET /api/runs/{id}/file serves + guards traversal")
from fastapi.testclient import TestClient   # noqa: E402
from main import app                          # noqa: E402
with TestClient(app) as client:
    r1 = client.get(f"/api/runs/{rid}/file", params={"rel": "p2object.rds", "download": 1})
    check("serves a run file", r1.status_code == 200 and r1.content == b"rds-bytes", f"{r1.status_code}")
    r2 = client.get(f"/api/runs/{rid}/file", params={"rel": "model/weights.bin"})
    check("serves a nested run file", r2.status_code == 200 and r2.content == b"weights", f"{r2.status_code}")
    r3 = client.get(f"/api/runs/{rid}/file", params={"rel": "../../../etc/passwd"})
    check("rejects path traversal", r3.status_code in (400, 404), f"{r3.status_code}")

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
    sys.exit(1)
print("d16 OK")
