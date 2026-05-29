"""
Run registration (entity-model v3 'Analysis run').

A Run groups a multi-step pipeline's outputs as ONE `analysis` entity that
spans turns, instead of a per-turn analysis each time. Verifies:
  - open_run / close_run / active_run_id lifecycle
  - harvested artifacts attach to the open Run (across turns)
  - executed cells accumulate onto the Run (recompute unit)
  - open_run rotates (closes prior); empty Run is discarded on close
  - the open_run / close_run typed tools
  - the background-job pin path (analysis_ctx.analysis_id)

Deterministic (no model). Run:
    .venv/bin/python tests/d15_run_registration.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d15_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d15.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from content.bio.lifecycle.runs import (                     # noqa: E402
    open_run, close_run, active_run_id,
)
from content.bio.lifecycle.registry import register_artifacts_from_tool_result  # noqa: E402
from content.bio.tools import open_run_tool, close_run_tool  # noqa: E402
from core.graph.entities import get_entity                   # noqa: E402

TID = "thr_d15"
_failures = []


def check(label, cond, detail=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + label + (f"  {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _fig_result(url, name, code):
    return ({"plots": [{"url": url, "original_name": name}], "execution_mode": "session"},
            {"code": code})


print("open_run lifecycle")
rid = open_run(TID, "pagoda2 clustering of GSM5746268", focus_entity_id=None)
e = get_entity(rid)
check("open_run creates an analysis entity", bool(e) and e["type"] == "analysis")
md = (e or {}).get("metadata") or {}
check("tagged thread_id + run_state=open", md.get("thread_id") == TID and md.get("run_state") == "open", str(md))
check("active_run_id returns it", active_run_id(TID) == rid)

print("\nartifacts attach to the open Run (and span turns)")
res, inp = _fig_result("/artifacts/umap.png", "umap.png", "# UMAP embedding\nsc.pl.umap(adata)")
new1 = register_artifacts_from_tool_result(tool_name="run_python", tool_input=inp, result_obj=res,
                                           focused_entity_id=None, analysis_ctx={"analysis_id": None}, thread_id=TID)
check("turn-1 figure parented to the Run", bool(new1) and new1[0]["parent_entity_id"] == rid, str(new1 and new1[0].get("parent_entity_id")))
check("cell code captured on the Run", "UMAP embedding" in (get_entity(rid).get("producing_code") or ""))

# A NEW turn = a fresh analysis_ctx; must still resolve to the SAME open Run.
res2, inp2 = _fig_result("/artifacts/clusters.png", "clusters.png", "# Leiden clusters")
new2 = register_artifacts_from_tool_result(tool_name="run_python", tool_input=inp2, result_obj=res2,
                                           focused_entity_id=None, analysis_ctx={"analysis_id": None}, thread_id=TID)
check("turn-2 figure same Run (spans turns)", bool(new2) and new2[0]["parent_entity_id"] == rid)
check("both cells accumulated", "Leiden clusters" in (get_entity(rid).get("producing_code") or ""))

print("\nrotation: a new open_run closes the prior (non-empty → kept)")
rid2 = open_run(TID, "differential expression", focus_entity_id=None)
prior = get_entity(rid)
check("prior Run closed", ((prior.get("metadata") or {}).get("run_state")) == "closed")
check("prior Run kept (had outputs)", prior["status"] != "archived")
check("new Run is active", active_run_id(TID) == rid2 and rid2 != rid)

print("\nclose_run discards an EMPTY Run")
closed = close_run(TID)
check("close returns the run id", closed == rid2)
check("empty Run discarded (archived)", get_entity(rid2)["status"] == "archived")
check("no active Run after close", active_run_id(TID) is None)

print("\ntyped tools (open_run / close_run via ctx)")
ctx = {"thread_id": TID, "focus_entity_id": None}
r = open_run_tool({"title": "ATAC peaks"}, ctx)
check("open_run_tool ok", r.get("status") == "ok" and active_run_id(TID) == r.get("run_id"), str(r))
check("open_run_tool requires a title", open_run_tool({}, ctx).get("error") is not None)
c = close_run_tool({}, ctx)
check("close_run_tool ok (empty → discarded)", c.get("status") == "ok")
check("close_run_tool noop when none open", close_run_tool({}, ctx).get("status") == "noop")

print("\nbackground-job pin path (analysis_ctx.analysis_id pins the Run)")
rid3 = open_run(TID, "job pipeline", focus_entity_id=None)
resj, inpj = _fig_result("/artifacts/job.png", "job.png", "# job output")
# Simulate runner.py's on_job_complete dispatch: analysis_id pre-set to the run.
newj = register_artifacts_from_tool_result(tool_name="run_python", tool_input=inpj, result_obj=resj,
                                           focused_entity_id=None, analysis_ctx={"analysis_id": rid3}, thread_id=TID)
check("job figure attaches to the pinned Run", bool(newj) and newj[0]["parent_entity_id"] == rid3)

print()
if _failures:
    print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
    sys.exit(1)
print("d15 OK")
