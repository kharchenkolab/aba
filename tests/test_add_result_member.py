"""add_result_member — the missing MCP tool for growing a Result.

Live friction (prj_8143327c thr_80190faf, 2026-06-12): user asked the
agent to add a second plot to res_44a48636. The agent looked up
result-type operations, found promote_to_result (wrong shape — creates
a NEW Result) and a docstring promise of add_result_member (lie — not
registered), then gave up: "I don't have a tool to add to an existing
Result." Now there's a real tool.

Three input shapes the test exercises:

  1. ref=<existing figure entity id>  — direct attach
  2. exec_id=<recent run>             — pin-then-attach in one call
  3. kind='text' + text=<…>           — inline text panel

Plus negative cases: bad result_id, type mismatch, missing inputs,
and remove_result_member round-trip.
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

_tmp = tempfile.mkdtemp(prefix="aba_add_member_")
os.environ["ABA_DB_PATH"]     = os.path.join(_tmp, "x.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"]    = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"]    = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"]        = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"]   = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db                              # noqa: E402
init_db()

import content.bio                                                  # noqa: E402,F401
from core.runtime.mcp import (                                       # noqa: E402
    register_inprocess_server, _reset_for_testing,
)
from content.bio.mcp_servers.aba_core import make_server             # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)


def _call(name: str, args: dict) -> dict:
    from content.bio.tools import execute_tool
    raw = execute_tool(name, args, {"thread_id": "thr_add_member"})
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed_result_with_one_figure() -> tuple[str, str]:
    """Returns (result_id, member_id) for a Result already holding one figure."""
    from core.graph.entities import create_entity
    from core.graph.edges import add_edge
    art = os.path.join(_tmp, "fig0.png")
    open(art, "w").write("x")
    fig0 = create_entity(
        entity_type="figure", title="UMAP integrated",
        artifact_path=art, metadata={"thread_id": "thr_add_member"},
    )
    rid = create_entity(
        entity_type="result", title="The Result",
        metadata={"thread_id": "thr_add_member",
                  "interpretation": "",
                  "members": [{"id": "m_0", "kind": "figure", "ref": fig0,
                                "caption": "first panel"}]},
    )
    add_edge(rid, fig0, "includes")
    return rid, "m_0"


def _members(rid: str) -> list[dict]:
    from core.graph.entities import get_entity
    return (get_entity(rid).get("metadata") or {}).get("members") or []


# ── shape 1: ref to an existing entity ──────────────────────────────────
def test_adds_existing_figure_by_ref():
    """Most common call: a figure entity already exists, attach it."""
    rid, _m0 = _seed_result_with_one_figure()
    from core.graph.entities import create_entity
    art = os.path.join(_tmp, "fig1.png")
    open(art, "w").write("y")
    fig1 = create_entity(
        entity_type="figure", title="Per-cluster entropy + UMAP",
        artifact_path=art, metadata={"thread_id": "thr_add_member"},
    )
    res = _call("add_result_member",
                {"result_id": rid, "kind": "figure", "ref": fig1,
                 "caption": "second panel"})
    assert res.get("status") == "ok", res
    assert res.get("entity_id") == fig1
    assert res.get("was_new") is False
    members = _members(rid)
    assert len(members) == 2
    assert members[-1]["ref"] == fig1
    assert members[-1]["caption"] == "second panel"


# ── shape 2: pin-then-attach from an exec_id ─────────────────────────────
def test_pins_loose_figure_from_exec_id_in_one_call():
    """Post-cutover (registry.py:132) run_python/run_r artifacts no
    longer auto-mint figure entities — they live in produced[] until
    pinned. add_result_member with exec_id should mint the entity AND
    attach it in one call (was_new=True)."""
    # Forge a minimal exec record that pin_artifact can consume.
    from core.graph import exec_records
    art = os.path.join(_tmp, "produced_fig.png")
    open(art, "w").write("z")
    cwd = os.path.join(_tmp, "work", "exec_test"); os.makedirs(cwd, exist_ok=True)
    from datetime import datetime, timezone
    exec_id = exec_records.create(
        thread_id="thr_add_member",
        tool_name="run_r",
        status="ok",
        code="ggplot(...)",
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        cwd=cwd,
        payload={"language": "r",
                  "produced": [{"kind": "figure", "url": art,
                                "original_name": "cluster_entropy_and_umap.png"}]},
    )

    rid, _m0 = _seed_result_with_one_figure()
    res = _call("add_result_member",
                {"result_id": rid, "kind": "figure",
                 "exec_id": exec_id, "artifact_idx": 0,
                 "caption": "from exec"})
    assert res.get("status") == "ok", res
    assert res.get("entity_id"), res
    assert res.get("was_new") is True, res
    members = _members(rid)
    assert len(members) == 2
    assert members[-1]["ref"] == res["entity_id"]
    assert members[-1]["caption"] == "from exec"


# ── shape 3: text panel ──────────────────────────────────────────────────
def test_adds_text_panel():
    """No entity reference needed; inline text panels are first-class."""
    rid, _m0 = _seed_result_with_one_figure()
    res = _call("add_result_member",
                {"result_id": rid, "kind": "text",
                 "text": "Note: clusters 20 and 15 are sample-skewed."})
    assert res.get("status") == "ok", res
    assert res.get("entity_id") is None
    members = _members(rid)
    assert members[-1]["kind"] == "text"
    assert "sample-skewed" in members[-1]["text"]


# ── insertion order ───────────────────────────────────────────────────────
def test_at_inserts_at_position():
    """`at` puts the new panel at the requested index instead of appending."""
    rid, _m0 = _seed_result_with_one_figure()
    res = _call("add_result_member",
                {"result_id": rid, "kind": "text", "text": "INTRO",
                 "at": 0})
    assert res.get("status") == "ok", res
    members = _members(rid)
    assert members[0]["kind"] == "text"
    assert members[0]["text"] == "INTRO"


# ── negative shapes ──────────────────────────────────────────────────────
def test_rejects_unknown_result():
    res = _call("add_result_member",
                {"result_id": "res_nope", "kind": "text", "text": "x"})
    assert "error" in res and "not found" in res["error"], res


def test_rejects_wrong_target_type():
    from core.graph.entities import create_entity
    fig = create_entity(entity_type="figure", title="solo",
                        artifact_path=os.path.join(_tmp, "solo.png"),
                        metadata={"thread_id": "thr_add_member"})
    open(os.path.join(_tmp, "solo.png"), "w").write("x")
    res = _call("add_result_member",
                {"result_id": fig, "kind": "text", "text": "x"})
    assert "error" in res and "not 'result'" in res["error"], res


def test_rejects_text_kind_without_text():
    rid, _m0 = _seed_result_with_one_figure()
    res = _call("add_result_member",
                {"result_id": rid, "kind": "text"})
    assert "error" in res, res
    assert "text" in res["error"].lower(), res


def test_rejects_figure_without_ref_or_exec_id():
    rid, _m0 = _seed_result_with_one_figure()
    res = _call("add_result_member",
                {"result_id": rid, "kind": "figure"})
    assert "error" in res, res
    assert "ref" in res["error"] and "exec_id" in res["error"], res


def test_rejects_kind_mismatch_on_ref():
    """ref pointing at a table while kind='figure' must reject — silent
    type coercion would hide a real agent mistake."""
    from core.graph.entities import create_entity
    tbl = create_entity(entity_type="table", title="some table",
                        artifact_path=os.path.join(_tmp, "tbl.csv"),
                        metadata={"thread_id": "thr_add_member"})
    open(os.path.join(_tmp, "tbl.csv"), "w").write("a,b\n")
    rid, _m0 = _seed_result_with_one_figure()
    res = _call("add_result_member",
                {"result_id": rid, "kind": "figure", "ref": tbl})
    assert "error" in res, res
    assert "table" in res["error"].lower()


# ── broadcast fires so the Result card refetches ────────────────────────
def test_broadcasts_entity_updated_on_add(monkeypatch):
    rid, _m0 = _seed_result_with_one_figure()
    seen: list = []
    from core.runtime import notifications
    monkeypatch.setattr(notifications, "broadcast",
                        lambda payload: seen.append(payload))
    res = _call("add_result_member",
                {"result_id": rid, "kind": "text", "text": "ping"})
    assert res.get("status") == "ok"
    matching = [p for p in seen
                if p.get("type") == "entity_updated"
                and p.get("entity_id") == rid
                and p.get("reason") == "member_added"]
    assert matching, f"no member_added broadcast. seen={seen}"


# ── remove_result_member round-trip ─────────────────────────────────────
def test_remove_result_member_unlinks_without_deleting_entity():
    rid, m0 = _seed_result_with_one_figure()
    # Add a second member, then remove the original
    from core.graph.entities import create_entity, get_entity
    art = os.path.join(_tmp, "fig_x.png")
    open(art, "w").write("y")
    fig_x = create_entity(entity_type="figure", title="x",
                          artifact_path=art,
                          metadata={"thread_id": "thr_add_member"})
    _call("add_result_member",
          {"result_id": rid, "kind": "figure", "ref": fig_x})
    assert len(_members(rid)) == 2
    res = _call("remove_result_member",
                {"result_id": rid, "member_id": m0})
    assert res.get("status") == "ok", res
    assert res.get("removed_member_id") == m0
    members = _members(rid)
    assert len(members) == 1
    # The figure entity itself must still exist (we only unlinked).
    from core.graph.entities import get_entity as _ge
    assert _ge(fig_x) is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
