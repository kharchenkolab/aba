"""Phase 2, 2B.5 — agent-tool creates carry derivation + an agent actor resolved
from the tool ctx's thread (the gateway thread can't see the ambient contextvar)."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase2_2b5_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"),
    "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "artifacts"),
    "ABA_WORK_DIR": str(Path(_tmp) / "work"),
    "DATA_DIR": str(Path(_tmp) / "data"),
})
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db   # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from core.graph.entities import create_entity, get_entity   # noqa: E402
from content.bio.lifecycle.runs import agent_actor_for_thread   # noqa: E402


def _open_run(thread_id):
    return create_entity(entity_type="analysis", title="run",
                         metadata={"thread_id": thread_id, "run_state": "open"})


def test_agent_actor_for_thread():
    assert agent_actor_for_thread(None) is None
    assert agent_actor_for_thread("no_run") is None
    rid = _open_run("T")
    assert agent_actor_for_thread("T") == f"agent:{rid}"


def test_create_claim_tool_derivation_and_actor():
    from content.bio.tools.curation import create_claim_tool
    rid = _open_run("Tc")
    out = create_claim_tool({"statement": "cells cluster"}, ctx={"thread_id": "Tc"})
    claim = get_entity(out["claim_id"])
    assert claim["derivation"] == {"kind": "manual"}            # no evidence
    assert claim["actor"] == f"agent:{rid}"
    out2 = create_claim_tool({"statement": "x", "evidence_ids": ["e1", "e2"]},
                             ctx={"thread_id": "Tc"})
    assert get_entity(out2["claim_id"])["derivation"] == {"kind": "derived_from",
                                                          "sources": ["e1", "e2"]}
