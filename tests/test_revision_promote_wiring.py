"""Revision → Run wiring + promoted-pin follow-through (2026-06-28 fix).

When a PROMOTED figure is revised via make_revision, the revision must:
  (A) get a wasGeneratedBy edge to its Run  (so it lands in figures/),
  (B) inherit the promoted Result's pin — primary_evidence_id + member
      refs move onto the revision (RunView reads primary_evidence_id to
      decide which output shows the red pin),
  (C) appear in the Run's output manifest as a figure output  (so the
      Plots strip lists it instead of dropping the superseded original).

repair_revision_wiring() backfills the same three for chains made before
the fix (e.g. the live prj_0590c5d8 that motivated it).

Pre-fix, make_revision created the revision via raw create_entity,
skipping all three — a revised+promoted figure vanished from the Plots
strip and figures/ with its pin stuck on the now-hidden original.

Run:  ABA_ENVS_DIR=<envs> .venv/bin/python tests/test_revision_promote_wiring.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_rev_wiring_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rw.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
# Honour a shell-supplied envs dir (this box); default to the CI path.
os.environ.setdefault("ABA_ENVS_DIR", "/workspace/aba-runtime/envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph import entities, edges                       # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


SEED = ("import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[1,2,3])\nplt.savefig('seed.png'); plt.close('all')\n")
REV = ("import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
       "plt.figure(); plt.plot([1,2,3],[9,8,7])\nplt.savefig('rev.png'); plt.close('all')\n")


def _seed_promote(tid: str):
    """Run seed code via a real kernel, then promote (pin → Result).
    Returns (parent_figure_id, result_id, run_id)."""
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    res = run_python({"code": SEED}, ctx={"thread_id": tid, "tool_use_id": f"tu_{tid}"})
    if res.get("returncode") != 0:
        raise RuntimeError(f"seed run failed: stderr={res.get('stderr')!r}")
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": SEED},
        result_obj=res, focused_entity_id=None, analysis_ctx={}, thread_id=tid)
    pinned = pin_artifact(res["exec_id"], "figure", 0, wrap_in_result=True, thread_id=tid)
    parent_id = pinned["entity_id"]
    result_id = pinned["result_id"]
    run_id = next((e["target_id"] for e in edges.edges_from(parent_id)
                   if e["rel_type"] == "wasGeneratedBy"), None)
    return parent_id, result_id, run_id


def _manifest_fig_ids(run_id: str) -> list[str]:
    run = entities.get_entity(run_id)
    outs = ((run or {}).get("metadata") or {}).get("run", {}).get("outputs") or []
    return [o.get("artifact_id") for o in outs if o.get("kind") == "figure"]


def _wgb_targets(eid: str) -> list[str]:
    return [e["target_id"] for e in edges.edges_from(eid)
            if e["rel_type"] == "wasGeneratedBy"]


def test_make_revision_wires_promoted_figure():
    print("\n[1] make_revision wires a promoted figure forward (edge + pin + manifest)")
    init_db()
    from content.bio.lifecycle.revisions import make_revision

    parent_id, result_id, run_id = _seed_promote("thr_rw1")
    check("run resolved from the promoted figure", bool(run_id), str(run_id))

    out = make_revision(parent_id, REV, thread_id="thr_rw1", title="Revised")
    new_id = out["new_entity_id"]
    rev_artifact = f"{out['exec_id']}:figure:0"

    # (A) wasGeneratedBy edge → the Run
    check("(A) revision wasGeneratedBy → run", _wgb_targets(new_id) == [run_id],
          str(_wgb_targets(new_id)))

    # (B) the promoted Result follows onto the revision
    md = entities.get_entity(result_id).get("metadata") or {}
    check("(B) result.primary_evidence_id → revision",
          md.get("primary_evidence_id") == new_id, str(md.get("primary_evidence_id")))
    check("(B) member ref → revision",
          [m.get("ref") for m in md.get("members") or []] == [new_id])
    check("(B) make_revision reports the re-anchor",
          any(m["result_id"] == result_id for m in out.get("reanchored_results") or []))

    # (C) the revision shows as a manifest figure output (head); the
    # original stays listed but the frontend hides it as superseded.
    ids = _manifest_fig_ids(run_id)
    check("(C) manifest lists the revision output", rev_artifact in ids, str(ids))

    # The original is the one the frontend will hide: it has the revision
    # as a descendant (revision_of), and the revision is NOT superseded.
    rev = entities.get_entity(new_id)
    check("revision points back at the parent (revision_of)",
          (rev.get("metadata") or {}).get("revision_of") == parent_id)
    check("revision is active (so it is the chain head)",
          rev.get("status") == "active")


def test_repair_revision_wiring_backfills():
    print("\n[2] repair_revision_wiring backfills a pre-fix chain (and is idempotent)")
    init_db()
    from content.bio.lifecycle.revisions import make_revision, repair_revision_wiring
    from core.graph.edges import remove_edge

    parent_id, result_id, run_id = _seed_promote("thr_rw2")
    out = make_revision(parent_id, REV, thread_id="thr_rw2", title="Revised2")
    new_id = out["new_entity_id"]

    # --- regress to the PRE-FIX broken state ---
    remove_edge(new_id, run_id, "wasGeneratedBy")                 # (A) un-wire
    md = dict(entities.get_entity(result_id).get("metadata") or {})
    md["primary_evidence_id"] = parent_id                         # (B) pin back on original
    for m in md.get("members") or []:
        if m.get("ref") == new_id:
            m["ref"] = parent_id
    entities.update_entity(result_id, metadata=md)

    check("pre: revision has no wasGeneratedBy", _wgb_targets(new_id) == [])
    check("pre: pin stuck on the original",
          (entities.get_entity(result_id).get("metadata") or {}).get("primary_evidence_id") == parent_id)

    # --- repair ---
    rep = repair_revision_wiring()
    check("post: revision wasGeneratedBy → run", _wgb_targets(new_id) == [run_id],
          str(_wgb_targets(new_id)))
    md2 = entities.get_entity(result_id).get("metadata") or {}
    check("post: pin re-anchored to the head", md2.get("primary_evidence_id") == new_id,
          str(md2.get("primary_evidence_id")))
    check("post: member ref re-anchored",
          [m.get("ref") for m in md2.get("members") or []] == [new_id])
    check("repair reports the edge added", any(x["figure"] == new_id for x in rep["edges_added"]))
    check("repair reports the re-anchor", any(x["result_id"] == result_id for x in rep["reanchored"]))
    check("repair refreshed the run manifest", run_id in rep["runs_refreshed"])

    # --- idempotent: a second pass finds nothing left to do ---
    rep2 = repair_revision_wiring()
    check("idempotent: no edges added on 2nd run", rep2["edges_added"] == [], str(rep2["edges_added"]))
    check("idempotent: no re-anchors on 2nd run", rep2["reanchored"] == [], str(rep2["reanchored"]))


def test_revision_of_unpromoted_figure_is_safe():
    print("\n[3] revising an UNPROMOTED figure still wires the run + no-ops the re-anchor")
    init_db()
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    from content.bio.lifecycle.revisions import make_revision

    tid = "thr_rw3"
    res = run_python({"code": SEED}, ctx={"thread_id": tid, "tool_use_id": "tu_rw3"})
    if res.get("returncode") != 0:
        raise RuntimeError(f"seed run failed: stderr={res.get('stderr')!r}")
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": SEED},
        result_obj=res, focused_entity_id=None, analysis_ctx={}, thread_id=tid)
    # Materialize WITHOUT wrapping in a Result (the stage5 fixture shape).
    parent_id = pin_artifact(res["exec_id"], "figure", 0,
                             wrap_in_result=False, thread_id=tid)["entity_id"]
    run_id = next((e["target_id"] for e in edges.edges_from(parent_id)
                   if e["rel_type"] == "wasGeneratedBy"), None)

    out = make_revision(parent_id, REV, thread_id=tid, title="Rev unpromoted")
    new_id = out["new_entity_id"]
    check("revision created", isinstance(new_id, str))
    check("revision still wired to run", _wgb_targets(new_id) == [run_id],
          str(_wgb_targets(new_id)))
    check("no Result → re-anchor is a no-op", out.get("reanchored_results") == [])
    # chain still navigable
    rev_edges = [e for e in edges.edges_from(new_id) if e["rel_type"] == "wasRevisionOf"]
    check("wasRevisionOf edge intact (exactly one)", len(rev_edges) == 1
          and rev_edges[0]["target_id"] == parent_id)


def main() -> int:
    test_make_revision_wires_promoted_figure()
    test_repair_revision_wiring_backfills()
    test_revision_of_unpromoted_figure_is_safe()
    print("\n" + ("ALL PASS" if not _failures else f"FAILURES: {_failures}"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
