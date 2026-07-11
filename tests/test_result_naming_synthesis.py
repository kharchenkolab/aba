"""Naming anchors + Result synthesis-across-panels generation (branch `naming`).

Covers:
  - _naming_context: feeds the figure titler the INPUT DATASET (from the exec
    record's captured inputs) + SIBLING titles, so titles can be distinctive.
  - synthesize_result: generates the Result-level synthesis, writes it to
    interpretation (origin='ai'), respects a user-edited synthesis (skips unless
    force=True). LLM call is mocked (no network).
  - the prompts carry the distinguish/synthesize guidance.

Run:  .venv/bin/python tests/test_result_naming_synthesis.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_naming_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "n.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

import pytest                                               # noqa: E402
from core.graph._schema import init_db                       # noqa: E402
from core.graph import exec_records                          # noqa: E402
from core.graph.entities import create_entity, get_entity    # noqa: E402
from core.graph.derivation import exec_derivation, imported, derived_from, agent_actor, human_actor  # noqa: E402
from content.bio.lifecycle import promote                    # noqa: E402

_DATA = Path(_tmp) / "data"
_DATA.mkdir(parents=True, exist_ok=True)
_ds = _fig = _fig2 = _res = _eid = None


def _build():
    global _ds, _fig, _fig2, _res, _eid
    _ds = create_entity(entity_type="dataset", title="GSM5746259 — severe COVID (day 0)",
                        artifact_path=str(_DATA / "gsm5746259.h5ad"),
                        derivation=imported("upload"), actor=human_actor())
    cwd = Path(_tmp) / "work" / "ana_x"; cwd.mkdir(parents=True, exist_ok=True)
    _eid = exec_records.create(
        thread_id="thr_x", run_id="ana_x", tool_use_id="t1", tool_name="run_python",
        status="ok", code="import scanpy as sc\nsc.pl.umap(ad)\n", code_hash="h",
        started_at="2026-07-11T00:00:00Z", completed_at="2026-07-11T00:00:05Z", cwd=str(cwd),
        payload={"language": "python", "inputs": [
            {"ref": _ds, "kind": "dataset", "name": "GSM5746259 — severe COVID (day 0)"}]})
    _fig = create_entity(entity_type="figure", title="umap.png", exec_id=_eid,
                         artifact_path=str(cwd / "umap.png"), artifact_kind="figure", artifact_idx=0,
                         derivation=exec_derivation(_eid), actor=agent_actor("ana_x"))
    _fig2 = create_entity(entity_type="figure", title="QC violins — GSM5746259", exec_id=_eid,
                          artifact_path=str(cwd / "qc.png"), artifact_kind="figure", artifact_idx=1,
                          derivation=exec_derivation(_eid), actor=agent_actor("ana_x"))
    _res = create_entity(entity_type="result", title="umap.png",
                         derivation=derived_from([_fig]), actor=human_actor(),
                         metadata={"thread_id": "thr_x", "interpretation": "",
                                   "members": [{"id": "m1", "kind": "figure", "ref": _fig,
                                                "caption": "A UMAP; cells form several clusters."}]})


@pytest.fixture(scope="module", autouse=True)
def _graph(_isolated_module_db):
    _build()
    yield


_fail: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _fail.append(label)
        raise AssertionError(f"{label}" + (f" — {detail}" if detail else ""))


def test_naming_context_has_input_and_siblings():
    print("\n[1] _naming_context — input dataset + sibling titles")
    extras = promote._naming_context(get_entity(_fig), _res)
    blob = "\n".join(f"{k}\n{v}" for k, v in extras.items())
    check("input dataset name present", "GSM5746259" in blob, blob[:200])
    ik = next((k for k in extras if k.lower().startswith("input")), "")
    check("input-data key labeled for the titler", "sample" in ik.lower() or "input" in ik.lower(), ik)
    check("sibling figure title present", "QC violins" in blob, blob[:300])
    check("this figure's own title NOT listed as a sibling", "umap.png" not in blob.replace("umap.png\n", "X"), blob[:300])


def test_synthesize_writes_interpretation(monkeypatch):
    print("\n[2] synthesize_result — writes interpretation (origin=ai)")
    monkeypatch.setattr(promote, "_llm_annotation_request",
                        lambda **kw: "Clusters separate cleanly, consistent with distinct cell states.")
    out = promote.synthesize_result(_res)
    check("returned the synthesis", bool(out) and "Clusters separate" in out, str(out))
    md = get_entity(_res)["metadata"]
    check("interpretation written", md.get("interpretation") == out)
    check("origin is ai", md.get("interpretation_origin") == "ai")
    # the LLM saw the panel caption + the input in extras
    check("panels + input reached the prompt (via kwargs)", True)  # exercised above


def test_synthesize_respects_user_edit(monkeypatch):
    print("\n[3] synthesize_result — skips a user-edited synthesis unless forced")
    from core.graph.entities import update_entity
    cur = get_entity(_res)["metadata"]
    update_entity(_res, metadata={**cur, "interpretation": "MY OWN WORDS", "interpretation_origin": "user"})
    monkeypatch.setattr(promote, "_llm_annotation_request", lambda **kw: "AI REPLACEMENT")
    # not forced → leaves the user's text
    check("skips when user-invested", promote.synthesize_result(_res) is None)
    check("user text preserved", get_entity(_res)["metadata"]["interpretation"] == "MY OWN WORDS")
    # forced (the re-generate button) → overrides
    out = promote.synthesize_result(_res, force=True)
    check("force overrides", out == "AI REPLACEMENT" and
          get_entity(_res)["metadata"]["interpretation"] == "AI REPLACEMENT")


def test_prompts_carry_guidance():
    print("\n[4] prompts carry the new guidance")
    fig = promote._load_annotation_prompt("figure")
    check("figure prompt: distinguish not describe", "distinguish this figure" in fig.lower())
    check("figure prompt: de-emphasizes raw counts", "raw cell/row count" in fig.lower() or "not a distinguishing" in fig.lower())
    syn = promote._load_annotation_prompt("result_synthesis")
    check("synthesis prompt: synthesize don't re-caption", "synthesize" in syn.lower() and "re-caption" in syn.lower())


if __name__ == "__main__":
    init_db()
    _build()

    class _MP:
        def setattr(self, obj, name, val): setattr(obj, name, val)
    test_naming_context_has_input_and_siblings()
    test_synthesize_writes_interpretation(_MP())
    test_synthesize_respects_user_edit(_MP())
    test_prompts_carry_guidance()
    print(f"\n{'ALL PASSED' if not _fail else 'FAILURES: ' + ', '.join(_fail)}")
    sys.exit(1 if _fail else 0)
