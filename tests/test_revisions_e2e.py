"""End-to-end: agent path for the 'come back to vN' workflow.

Re-runs the exact dispatcher path the live agent uses (execute_tool,
which routes through the in-process MCP gateway) to confirm:

  1. list_revisions, called via execute_tool, returns the labeled v1…vN
     chain — the addressing primitive the agent lacked in the 2026-06-11
     live session (prj_128380fd thr_deed230d).
  2. set_current_revision, called via execute_tool, performs the
     non-destructive switch without the agent having to issue four
     delete_revision calls in a row.
  3. The chain is restorable: a second set_current_revision call back
     to the original head un-supersedes everything.

Mirrors the test_update_entity_fields_broadcast.py pattern.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_rev_e2e_")
os.environ["ABA_DB_PATH"] = os.path.join(_tmp, "e2e.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"] = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"] = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"] = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db                                       # noqa: E402
init_db()

import content.bio                                                           # noqa: E402,F401

from core.runtime.mcp import register_inprocess_server, _reset_for_testing    # noqa: E402
from content.bio.mcp_servers.aba_core import make_server                       # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)


def _call(name: str, args: dict) -> dict:
    from content.bio.tools import execute_tool
    raw = execute_tool(name, args, {"thread_id": "thr_e2e"})
    return json.loads(raw) if isinstance(raw, str) else raw


def _build_chain(n: int) -> tuple[list[str], str]:
    """Make a chain of `n` figures and one Result whose member.ref
    anchors to the head. Returns (ids_oldest_first, result_id)."""
    import time
    from core.graph.entities import create_entity
    from core.graph.edges import add_edge
    ids: list[str] = []
    for i in range(n):
        p = os.path.join(_tmp, f"e2e_{i}.png")
        open(p, "w").write("x")
        ids.append(create_entity(
            entity_type="figure",
            title="UMAP of cells",   # identical titles, mirroring the live bug
            artifact_path=p,
            metadata={"thread_id": "thr_e2e"},
        ))
        time.sleep(0.011)  # SQLite TIMESTAMP has 1s resolution; jitter for ordering
    for i in range(1, n):
        add_edge(source_id=ids[i], target_id=ids[i-1], rel_type="wasRevisionOf")
    rid = create_entity(
        entity_type="result", title="The Result",
        metadata={"thread_id": "thr_e2e",
                  "members": [{"id": "m1", "kind": "figure", "ref": ids[-1]}]},
    )
    add_edge(source_id=rid, target_id=ids[-1], rel_type="includes")
    return ids, rid


def _member_ref(rid: str) -> str:
    from core.graph.entities import get_entity
    return ((get_entity(rid).get("metadata") or {})
            .get("members") or [{}])[0].get("ref")


def test_list_revisions_via_execute_tool_returns_labeled_chain():
    """The agent's call path: execute_tool('list_revisions', {entity_id}).
    The 7-revision chain that triggered the live bug — confirm labels
    line up with the chevron strip."""
    ids, _rid = _build_chain(7)

    res = _call("list_revisions", {"entity_id": ids[0]})  # pass oldest id
    assert res.get("total") == 7, res
    assert res.get("current_id") == ids[-1], res
    revs = res.get("revisions") or []
    # Newest first; v7 is the head.
    assert revs[0]["version"] == 7 and revs[0]["id"] == ids[-1]
    assert revs[0]["is_current"] is True
    # v1 is the tail.
    assert revs[-1]["version"] == 1 and revs[-1]["id"] == ids[0]
    assert revs[-1]["is_current"] is False


def test_set_current_revision_via_execute_tool_switches_anchor():
    """The user says 'go back to v4'. The agent maps via list_revisions
    then switches via set_current_revision — non-destructively."""
    ids, rid = _build_chain(7)

    listing = _call("list_revisions", {"entity_id": ids[0]})
    v4 = next(r for r in listing["revisions"] if r["version"] == 4)
    assert v4["id"] == ids[3], v4

    res = _call("set_current_revision", {"entity_id": ids[3]})
    assert res.get("current_id") == ids[3], res
    assert set(res.get("superseded") or []) == {ids[6], ids[5], ids[4]}, res
    assert _member_ref(rid) == ids[3]

    # The chevron strip now shows 4 versions.
    relisting = _call("list_revisions", {"entity_id": ids[0]})
    assert relisting.get("total") == 4
    assert relisting.get("current_id") == ids[3]

    # Reversible: bounce back to v7. Hidden ones are restored.
    back = _call("set_current_revision", {"entity_id": ids[6]})
    assert set(back.get("restored") or []) == {ids[6], ids[5], ids[4]}, back
    assert _member_ref(rid) == ids[6]
    final = _call("list_revisions", {"entity_id": ids[0]})
    assert final.get("total") == 7


def test_get_provenance_at_default_reaches_all_six_ancestors():
    """The depth=3 cap was the second source of friction. Default depth
    bumped to 8 in Slice B — a 7-revision chain should fully unwrap."""
    ids, _rid = _build_chain(7)
    res = _call("get_provenance", {"entity_id": ids[-1]})
    graph = res.get("graph") or []
    assert len(graph) == 6, f"expected 6 ancestors at default depth, got {len(graph)}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
