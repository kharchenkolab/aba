"""Entity-mgmt refactor Phase 2 — generic read_entity primitive.

The read_entity tool is the YAML-driven generic surface that lets the
agent read ANY entity's curated fields without a per-type tool. Covers
the long-standing 'agent can't read auto-generated captions' gap
(see misc/entity_mgmt_refactor.md / [[feedback_agent_entity_parity]]).

Coverage:
  - Figure → returns artifact_path, exec_id, parent_summary per
    figure.yaml agent_sees
  - Result with a figure member → members_summary INCLUDES caption +
    caption_origin (the read-blindness gap)
  - Result with a figure member that has a revision chain → member's
    displayed_id is chain[0] (latest), not the anchor ref
  - Claim → returns caveats, alternatives, evidence_summary
  - Finding → returns evidence_summary from supports edges
  - Dataset → returns source, organism, file_count per dataset.yaml
  - Unknown entity_id → {"error": "..."}
  - Explicit fields=[...] selection → only those fields returned
  - Field outside agent_sees but valid → returns via fallback projector
    (e.g. fields=["created_at"] on a figure)

Run: .venv/bin/python tests/test_read_entity.py
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_read_entity_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "r.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, set_db_path  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402  (loads entity_types + skills)

from core.runtime.mcp import register_inprocess_server, _reset_for_testing  # noqa: E402
from content.bio.mcp_servers.aba_core import make_server  # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)

from content.bio.tools import execute_tool  # noqa: E402
from core.graph.entities import create_entity, get_entity, update_entity  # noqa: E402
from core.graph.edges import add_edge  # noqa: E402

CTX = {"thread_id": "default"}
_failures: list[str] = []


def call(name, **inp):
    return json.loads(execute_tool(name, inp, CTX))


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    # ── Figure ──────────────────────────────────────────────────────
    print("read figure")
    art = os.path.join(_tmp, "fig.png")
    open(art, "w").write("x")
    fig_id = create_entity(
        entity_type="figure", title="UMAP day 0",
        artifact_path=art, exec_id="exec_abc",
        metadata={"thread_id": "default"},
    )
    r = call("read_entity", entity_id=fig_id)
    check("figure read returns id+type+title",
          r.get("id") == fig_id and r.get("type") == "figure" and r.get("title") == "UMAP day 0",
          str(r)[:200])
    f = r.get("fields", {})
    check("figure agent_sees includes artifact_path",
          f.get("artifact_path") == art, str(f)[:200])
    check("figure agent_sees includes exec_id",
          f.get("exec_id") == "exec_abc", str(f))
    check("figure agent_sees includes tags (empty list default)",
          isinstance(f.get("tags"), list))
    check("figure agent_sees includes advisor_notes (empty default)",
          isinstance(f.get("advisor_notes"), list))

    # ── Result with figure member: caption surfacing ────────────────
    print("read Result with caption-bearing member")
    res_id = create_entity(
        entity_type="result", title="Monocyte expansion",
        metadata={
            "thread_id": "default",
            "interpretation": "Day-0 PBMCs show monocyte expansion",
            "interpretation_origin": "ai",
            "members": [
                {"id": "m1", "kind": "figure", "ref": fig_id,
                 "caption": "Auto-generated: UMAP colored by cluster.",
                 "caption_origin": "auto"},
            ],
        },
    )
    r = call("read_entity", entity_id=res_id)
    f = r.get("fields", {})
    check("result interpretation surfaced",
          f.get("interpretation") == "Day-0 PBMCs show monocyte expansion")
    ms = f.get("members_summary") or []
    check("members_summary is a list with 1 entry",
          isinstance(ms, list) and len(ms) == 1, str(ms))
    m0 = ms[0] if ms else {}
    check("member has ref (figure id)", m0.get("ref") == fig_id, str(m0))
    check("member has CAPTION (the read-blindness gap fix)",
          m0.get("caption") == "Auto-generated: UMAP colored by cluster.",
          str(m0))
    check("member has caption_origin", m0.get("caption_origin") == "auto", str(m0))
    check("member has title (looked up from ref)",
          m0.get("title") == "UMAP day 0", str(m0))
    check("member has artifact_path (looked up from ref)",
          m0.get("artifact_path") == art, str(m0))

    # ── Revision chain: displayed_id is latest, not anchor ─────────
    print("Result with revision chain → displayed_id = latest")
    art2 = os.path.join(_tmp, "fig2.png")
    open(art2, "w").write("y")
    fig2_id = create_entity(
        entity_type="figure", title="UMAP day 0 (v2)",
        artifact_path=art2, exec_id="exec_def",
        metadata={"thread_id": "default", "wasRevisionOf": fig_id},
    )
    # supersede chain: fig (anchor) -> fig2 (latest)
    add_edge(source_id=fig2_id, target_id=fig_id, rel_type="wasRevisionOf")
    r = call("read_entity", entity_id=res_id)
    ms = r.get("fields", {}).get("members_summary") or []
    m0 = ms[0] if ms else {}
    # Chain head is the latest revision (fig2). If figure_history works
    # off the anchor (fig_id) the chain returns both; chain[0] = fig2.
    check("member.displayed_id is the latest revision (fig2)",
          m0.get("displayed_id") == fig2_id,
          f"got displayed_id={m0.get('displayed_id')}, fig_id={fig_id}, fig2_id={fig2_id}")
    check("member.chain_length is 2",
          m0.get("chain_length") == 2, str(m0))

    # ── Claim ───────────────────────────────────────────────────────
    print("read Claim with caveats + evidence")
    claim_id = create_entity(
        entity_type="claim", title="Severe COVID expands monocytes",
        metadata={
            "thread_id": "default",
            "statement": "Severe COVID expands classical monocytes in PBMCs.",
            "confidence": "moderate",
            "caveats": ["small n", "single timepoint"],
            "alternatives": ["sampling bias from severity-stratified cohort"],
            "status_log": [
                {"at": "2026-06-08T10:00", "from": "draft", "to": "open"},
            ],
        },
    )
    add_edge(source_id=claim_id, target_id=res_id, rel_type="supports")
    r = call("read_entity", entity_id=claim_id)
    f = r.get("fields", {})
    check("claim statement surfaced",
          f.get("statement", "").startswith("Severe COVID"))
    check("claim caveats list",
          f.get("caveats") == ["small n", "single timepoint"])
    check("claim alternatives list",
          f.get("alternatives") == ["sampling bias from severity-stratified cohort"])
    check("claim confidence", f.get("confidence") == "moderate")
    ev = f.get("evidence_summary") or []
    check("claim evidence_summary lists supported entity",
          isinstance(ev, list) and any(x.get("id") == res_id for x in ev),
          str(ev))

    # ── Finding ─────────────────────────────────────────────────────
    print("read Finding")
    finding_id = create_entity(
        entity_type="finding", title="Monocyte expansion observation",
        metadata={
            "thread_id": "default",
            "text": "COVID day-0 PBMCs show monocyte expansion vs healthy.",
        },
    )
    add_edge(source_id=finding_id, target_id=res_id, rel_type="supports")
    r = call("read_entity", entity_id=finding_id)
    f = r.get("fields", {})
    check("finding text surfaced",
          (f.get("text") or "").startswith("COVID day-0"), str(f))
    ev = f.get("evidence_summary") or []
    check("finding evidence_summary lists result",
          any(x.get("id") == res_id for x in ev), str(ev))

    # ── Dataset ─────────────────────────────────────────────────────
    print("read Dataset")
    ds_path = os.path.join(_tmp, "counts.h5ad")
    open(ds_path, "w").write("x")
    ds_id = create_entity(
        entity_type="dataset", title="GSM5746259 COVID PBMC",
        artifact_path=ds_path,
        metadata={
            "thread_id": "default",
            "source": "GEO:GSM5746259",
            "organism": "Homo sapiens",
            "file_count": 1,
            "size_bytes": 7532,
            "description": "Single-cell PBMCs from severe COVID, day 0.",
        },
    )
    r = call("read_entity", entity_id=ds_id)
    f = r.get("fields", {})
    check("dataset source", f.get("source") == "GEO:GSM5746259")
    check("dataset organism", f.get("organism") == "Homo sapiens")
    check("dataset file_count", f.get("file_count") == 1)
    check("dataset description", "PBMCs" in (f.get("description") or ""))

    # ── Unknown entity ──────────────────────────────────────────────
    print("read unknown entity → error")
    r = call("read_entity", entity_id="ent_nonexistent")
    check("unknown returns error",
          "error" in r and "not found" in r["error"], str(r))

    # ── Explicit fields filter ──────────────────────────────────────
    print("explicit fields=[...] returns only requested")
    r = call("read_entity", entity_id=claim_id, fields=["caveats"])
    f = r.get("fields", {})
    check("only 'caveats' key present",
          set(f.keys()) == {"caveats"}, str(f))
    check("caveats value matches", f.get("caveats") == ["small n", "single timepoint"])

    # ── Fallback projector for field outside agent_sees ─────────────
    print("fallback projector for non-agent_sees field")
    r = call("read_entity", entity_id=fig_id, fields=["created_at", "status"])
    f = r.get("fields", {})
    check("created_at returned (top-level column fallback)",
          f.get("created_at") is not None, str(f))
    check("status returned", f.get("status") is not None, str(f))

    # ── Unknown field → None, no error ─────────────────────────────
    print("unknown field name → None, no error")
    r = call("read_entity", entity_id=fig_id, fields=["does_not_exist_field"])
    f = r.get("fields", {})
    check("unknown field returns None",
          f.get("does_not_exist_field") is None and "error" not in r, str(r))

    # ══════════════════════════════════════════════════════════════
    # Phase 4: update_entity_fields
    # ══════════════════════════════════════════════════════════════
    print("update_entity_fields: top-level + metadata mixed")
    r = call("update_entity_fields", entity_id=res_id,
             fields={"title": "Monocyte expansion (revised)",
                     "interpretation": "Revised by agent",
                     "tags": ["covid", "pbmc"]})
    check("update ok", r.get("status") == "ok", str(r))
    check("updated lists keys we sent",
          set(r.get("updated") or []) ==
          {"title", "interpretation", "tags"}, str(r))
    e = get_entity(res_id)
    check("title persisted", e.get("title") == "Monocyte expansion (revised)")
    check("tags persisted", e.get("tags") == ["covid", "pbmc"])
    check("interpretation persisted in metadata",
          (e.get("metadata") or {}).get("interpretation") == "Revised by agent")

    print("update_entity_fields: claim caveats whole-list replace")
    r = call("update_entity_fields", entity_id=claim_id,
             fields={"caveats": ["replicate in larger cohort"]})
    check("claim caveats update ok", r.get("status") == "ok", str(r))
    e = get_entity(claim_id)
    check("caveats replaced",
          (e.get("metadata") or {}).get("caveats") == ["replicate in larger cohort"])

    print("update_entity_fields: rejects disallowed field")
    r = call("update_entity_fields", entity_id=fig_id,
             fields={"artifact_path": "/tmp/different.png"})
    check("artifact_path rejected for figure",
          "error" in r and "not editable" in r["error"], str(r))
    check("error names the type", "figure" in r.get("error", ""), str(r))
    check("error lists allowed fields", "Allowed:" in r.get("error", ""), str(r))

    print("update_entity_fields: type with no agent_can_update → error")
    # A plan entity has no agent_can_update declared; should reject.
    plan_id = create_entity(entity_type="plan", title="t",
                            metadata={"thread_id": "default"})
    r = call("update_entity_fields", entity_id=plan_id,
             fields={"title": "different"})
    check("plan reject mentions type",
          "error" in r and "not editable" in r.get("error", "")
          and "plan" in r.get("error", ""), str(r))
    check("plan reject points at list_entity_operations",
          "list_entity_operations" in r.get("error", ""), str(r))

    print("update_entity_fields: unknown entity → error")
    r = call("update_entity_fields", entity_id="ent_nope",
             fields={"title": "x"})
    check("unknown entity error",
          "error" in r and "not found" in r["error"], str(r))

    print("update_entity_fields: empty fields dict → error")
    r = call("update_entity_fields", entity_id=res_id, fields={})
    check("empty fields rejected",
          "error" in r and "non-empty" in r["error"], str(r))

    print("update_entity_fields: empty-string title → error")
    r = call("update_entity_fields", entity_id=res_id, fields={"title": "   "})
    check("blank title rejected",
          "error" in r and "title cannot be empty" in r["error"], str(r))

    print("update_entity_fields: None on metadata key removes it")
    # First set description on dataset, then clear it.
    call("update_entity_fields", entity_id=ds_id, fields={"description": "temp"})
    r = call("update_entity_fields", entity_id=ds_id, fields={"description": None})
    e = get_entity(ds_id)
    check("description cleared from metadata",
          "description" not in (e.get("metadata") or {}), str(e.get("metadata")))

    print("update_entity_fields: read-after-write via read_entity")
    r = call("read_entity", entity_id=res_id, fields=["title", "interpretation"])
    f = r.get("fields", {})
    check("read sees updated title",
          f.get("title") == "Monocyte expansion (revised)", str(f))
    check("read sees updated interpretation",
          f.get("interpretation") == "Revised by agent", str(f))

    # ══════════════════════════════════════════════════════════════
    # Phase 5: list_entity_operations
    # ══════════════════════════════════════════════════════════════
    print("list_entity_operations: by type")
    r = call("list_entity_operations", entity_type="result")
    check("returns type", r.get("type") == "result", str(r))
    check("readable matches agent_sees",
          "members_summary" in (r.get("readable") or []), str(r.get("readable")))
    check("writable matches agent_can_update",
          set(r.get("writable") or []) ==
          {"title", "notes", "tags", "interpretation"}, str(r.get("writable")))
    check("workflow_tools present",
          "promote_to_result" in (r.get("workflow_tools") or []), str(r))
    check("user_gestures.chat present",
          isinstance(r.get("user_gestures", {}).get("chat"), list), str(r))
    check("status_states present",
          "active" in (r.get("status_states") or []), str(r))

    print("list_entity_operations: by entity_id (lookup type)")
    r = call("list_entity_operations", entity_id=claim_id)
    check("type resolved from entity_id",
          r.get("type") == "claim", str(r))
    check("claim writable includes caveats/alternatives",
          set(r.get("writable") or []) >= {"caveats", "alternatives"}, str(r))

    print("list_entity_operations: unknown type → error")
    r = call("list_entity_operations", entity_type="nonexistent_type")
    check("unknown type error", "error" in r, str(r))

    print("list_entity_operations: no args → error")
    r = call("list_entity_operations")
    check("no-args error",
          "error" in r and ("entity_type" in r["error"]
                            or "entity_id" in r["error"]), str(r))

    print("list_entity_operations: figure → workflow tools include run_python/run_r")
    r = call("list_entity_operations", entity_type="figure")
    check("figure agent_tools",
          "run_python" in (r.get("workflow_tools") or []), str(r))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL ENTITY-OPS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
