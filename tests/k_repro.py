"""
Kernel-adjacent platform items (kernels.md §8):
  1. Summarization retains run_python/run_r cell code (state remains
     explainable/replayable after old messages are summarized).
  2. Registered artifacts are tagged with execution_mode (session vs stateless)
     + reproducible, so reproduction knows what it needs.

Deterministic (no model — _summarize is monkeypatched).
Run:
    .venv/bin/python tests/k_repro.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_krepro_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "k.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
import content.bio.lifecycle.registry as reg                 # noqa: E402
import core.summarize.rolling as rolling                     # noqa: E402
from core.summarize.rolling import extract_session_cells, effective_history  # noqa: E402
from core.exec.run import run_python_code                    # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_cell_retention():
    print("item 1: summarization retains cell code")
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "analyze this"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "loading"},
            {"type": "tool_use", "name": "run_python", "input": {"code": "import pandas as pd\nDF = pd.read_csv('x')"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
        {"role": "assistant", "content": [
            {"type": "tool_use", "name": "run_r", "input": {"code": "library(DESeq2)\ndds <- DESeqDataSet(x)"}}]},
    ]
    cells = extract_session_cells(msgs)
    check("extracts python + r cells", "DF = pd.read_csv" in cells and "DESeqDataSet" in cells, cells[:120])
    check("language-fenced", "```python" in cells and "```r" in cells)
    check("ignores non-code messages", "analyze this" not in cells)

    # effective_history keeps the cells when it summarizes (model stubbed).
    rolling._summarize = lambda old, prior: "CANNED SUMMARY"
    long_msgs = []
    for i in range(40):
        if i == 3:
            long_msgs.append({"role": "assistant", "content": [
                {"type": "tool_use", "name": "run_python", "input": {"code": "DDS_BUILT = run_deseq2()"}}]})
        else:
            long_msgs.append({"role": "user" if i % 2 == 0 else "assistant",
                              "content": [{"type": "text", "text": f"msg {i}"}]})
    out = effective_history("workspace", long_msgs)
    head = json.dumps(out[0])  # the summary message
    check("summary message carries the prose summary", "CANNED SUMMARY" in head)
    check("summary message retains the old cell code", "DDS_BUILT = run_deseq2()" in head, head[:160])


def test_execution_mode_tag():
    print("item 2: execution_mode / reproducible tagging")
    r = run_python_code("print('hi')", project_id="single", run_id="rp1")
    check("stateless run tags execution_mode=stateless", r.get("execution_mode") == "stateless", str(r)[:120])

    # session-derived artifact → reproducible:session
    res_session = {"plots": [{"url": "/artifacts/v.png", "original_name": "volcano.png"}],
                   "execution_mode": "session"}
    recs = reg.register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": "c"}, result_obj=res_session,
        focused_entity_id=None, analysis_ctx={"analysis_id": None, "turn_index": 0})
    md = (recs[0].get("metadata") or {}) if recs else {}
    check("session artifact tagged execution_mode=session", md.get("execution_mode") == "session", str(md))
    check("session artifact reproducible=session", md.get("reproducible") == "session")

    # stateless artifact → reproducible:self_contained
    res_stateless = {"tables": [{"url": "/artifacts/t.csv", "original_name": "de.csv"}],
                     "execution_mode": "stateless"}
    recs2 = reg.register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": "c"}, result_obj=res_stateless,
        focused_entity_id=None, analysis_ctx={"analysis_id": None, "turn_index": 0})
    md2 = (recs2[0].get("metadata") or {}) if recs2 else {}
    check("stateless artifact reproducible=self_contained", md2.get("reproducible") == "self_contained", str(md2))

    # run_r artifacts now register too
    res_r = {"plots": [{"url": "/artifacts/rp.png", "original_name": "rplot.png"}], "execution_mode": "session"}
    recs3 = reg.register_artifacts_from_tool_result(
        tool_name="run_r", tool_input={"code": "plot()"}, result_obj=res_r,
        focused_entity_id=None, analysis_ctx={"analysis_id": None, "turn_index": 0})
    check("run_r artifacts register", len(recs3) >= 1 and recs3[0].get("type") == "figure", str(recs3)[:120])


def main() -> int:
    init_db()
    test_cell_retention()
    test_execution_mode_tag()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL KERNEL-ADJACENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
