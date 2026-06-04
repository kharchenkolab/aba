"""
Entity-operation tools (UI parity): the agent must be able to manage the
project's own entities — register a dataset, pin, promote a figure to a result,
build findings/claims, tag/annotate — the same actions a user can take in the
UI. (A real session exposed that the agent had NO tool to "register a dataset"
and improvised file-dumps; the dataset entity was never created.)

Runs against an ISOLATED temp DB — never a live project DB (DB-safety). The
Skeptic advisor is faked so promote_to_result doesn't reach for the model.

Deterministic (no model). Run:
    .venv/bin/python tests/d8_entity_ops.py
"""
from __future__ import annotations
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_ent_")
from core.graph import _schema  # noqa: E402
_schema.set_db_path(os.path.join(_tmp, "test.db"))
_schema.init_db()

import content.bio  # noqa: E402,F401  (registers skills incl. core/manage-entities)
import content.bio.advisors.runner as _runner  # noqa: E402
_runner.skeptic_review = lambda *a, **k: None   # fake the advisor

from content.bio.tools import execute_tool, TOOL_SCHEMAS  # noqa: E402
# Phase 6.I: tools route via aba_core in-process MCP server; the
# legacy EXECUTORS dict is now empty. The assertion below switched
# from "in EXECUTORS" to "is_inprocess_tool" — same intent (the
# 7 entity ops have an active dispatch target).
from core.runtime.mcp import register_inprocess_server, is_inprocess_tool, _reset_for_testing  # noqa: E402
from content.bio.mcp_servers.aba_core import make_server  # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)
from core.skills.loader import get_skill  # noqa: E402
from core.graph.entities import create_entity, get_entity  # noqa: E402

CTX = {"thread_id": "default"}
NEW = ["list_entities", "register_dataset", "pin_entity", "promote_to_result",
       "create_finding", "create_claim", "annotate_entity"]
_failures: list[str] = []


def call(name, **inp):
    return json.loads(execute_tool(name, inp, CTX))


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    print("registration")
    names = {t["name"] for t in TOOL_SCHEMAS}
    check("all 7 entity tools in TOOL_SCHEMAS", set(NEW) <= names, str(set(NEW) - names))
    # Phase 6.I: replaced "in EXECUTORS" — tools now route via aba_core
    # MCP. is_inprocess_tool returns True when aba_core has the handler.
    not_on_aba_core = {n for n in NEW if not is_inprocess_tool(n)}
    check("all 7 entity tools registered on aba_core",
          not not_on_aba_core, str(not_on_aba_core))
    sk = get_skill("manage-entities")
    check("manage-entities core skill loaded", sk is not None)
    check("  ...visibility=always (always in prompt)", bool(sk) and sk.visibility == "always",
          sk.visibility if sk else "—")

    print("register_dataset (the op that failed in the wild)")
    f = os.path.join(_tmp, "counts.h5ad"); open(f, "w").write("x")
    r = call("register_dataset", path=f, title="GSM5746259 COVID PBMC",
             source="GEO:GSM5746259", summary="7532x36601", producing_code="import scanpy as sc")
    check("dataset created", r.get("status") == "ok" and r.get("dataset_id"), str(r))
    did = r.get("dataset_id")
    e = get_entity(did) if did else None
    check("  ...entity type=dataset", bool(e) and e["type"] == "dataset")
    check("  ...provenance captured (source + producing_code)",
          bool(e) and (e.get("metadata") or {}).get("source") == "GEO:GSM5746259" and e.get("producing_code"))

    print("list_entities (find ids to operate on)")
    r = call("list_entities", type="dataset")
    check("lists the dataset", any(x["id"] == did for x in r.get("entities", [])), str(r)[:200])

    print("pin_entity")
    r = call("pin_entity", entity_id=did)
    check("pinned ok + persisted", r.get("status") == "ok" and bool(get_entity(did).get("pinned")), str(r))
    check("missing entity -> error", "error" in call("pin_entity", entity_id="nope_404"))

    print("promote_to_result")
    fig = create_entity(entity_type="figure", title="UMAP", metadata={"thread_id": "default"})
    r = call("promote_to_result", figure_id=fig, interpretation="Monocyte expansion in COVID day 0.")
    check("figure -> result", r.get("status") == "ok" and r.get("result_id"), str(r))
    rid = r.get("result_id")
    check("  ...result entity exists", bool(get_entity(rid)) and get_entity(rid)["type"] == "result")
    check("non-figure -> error", "error" in call("promote_to_result", figure_id=did, interpretation="x"))

    print("create_finding + create_claim")
    r = call("create_finding", result_ids=[rid], text="COVID day-0 PBMCs show monocyte expansion.")
    check("finding created", r.get("status") == "ok" and get_entity(r.get("finding_id", ""))["type"] == "finding", str(r))
    fid = r.get("finding_id")
    r = call("create_claim", statement="Severe COVID expands classical monocytes in PBMCs.", evidence_ids=[fid])
    check("claim created", r.get("status") == "ok" and get_entity(r.get("claim_id", ""))["type"] == "claim", str(r))

    print("annotate_entity")
    r = call("annotate_entity", entity_id=did, tags=["pbmc", "covid"], notes="day 0")
    e = get_entity(did)
    check("tags + notes persisted", r.get("status") == "ok" and e.get("tags") == ["pbmc", "covid"] and e.get("notes") == "day 0",
          str({"tags": e.get("tags"), "notes": e.get("notes")}))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures)); return 1
    print("ALL ENTITY-OPS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
