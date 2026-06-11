"""update_member_caption — the missing tool for per-figure captions.

Live bug (prj_128380fd thr_deed230d, 2026-06-11): the user asked the
agent to "update the caption" of a Result whose first member was a
figure. The agent reached for update_entity_fields with
{interpretation: ...} because that was the only caption-shaped field
the type's agent_can_update slot exposed. Result: the OLD per-figure
caption stayed under the image, and the NEW prose appeared as a
SECOND text block above. Two text fields, both visible, both kind-of-
captions, no easy disambiguation.

The fix is a typed tool — update_member_caption — that writes
members[i].caption directly, with an explicit docstring redirect from
update_entity_fields when the user mentions a "figure caption".

This test drives the same dispatcher path the agent uses and checks:
  1. the right field is updated (members[i].caption, NOT interpretation),
  2. an entity_updated broadcast fires so the Result card refetches,
  3. bad inputs are rejected with explanatory errors.
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

_tmp = tempfile.mkdtemp(prefix="aba_member_cap_")
os.environ["ABA_DB_PATH"]     = os.path.join(_tmp, "mc.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"]    = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"]    = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"]        = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"]   = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db                                       # noqa: E402
init_db()

import content.bio                                                           # noqa: E402,F401
from core.runtime.mcp import register_inprocess_server, _reset_for_testing    # noqa: E402
from content.bio.mcp_servers.aba_core import make_server                       # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)


def _call(name: str, args: dict) -> dict:
    from content.bio.tools import execute_tool
    raw = execute_tool(name, args, {"thread_id": "thr_member_cap"})
    return json.loads(raw) if isinstance(raw, str) else raw


def _seed_result_with_figure_member() -> tuple[str, str, str]:
    """Returns (result_id, member_id, figure_id). Mirrors the shape
    that produced the live bug — a Result with one figure member whose
    caption is the auto-generated short description."""
    from core.graph.entities import create_entity
    from core.graph.edges import add_edge
    art = os.path.join(_tmp, "fig.png")
    open(art, "w").write("x")
    fig = create_entity(
        entity_type="figure", title="UMAP of cells",
        artifact_path=art, metadata={"thread_id": "thr_member_cap"},
    )
    rid = create_entity(
        entity_type="result", title="The Result",
        metadata={"thread_id": "thr_member_cap",
                  "interpretation": "old interpretation block",
                  "members": [{"id": "m_abc", "kind": "figure", "ref": fig,
                                "caption": "auto: 13 clusters in UMAP",
                                "caption_origin": "ai"}]},
    )
    add_edge(rid, fig, "includes")
    return rid, "m_abc", fig


def _members_summary(rid: str) -> list[dict]:
    res = _call("read_entity", {"entity_id": rid,
                                 "fields": ["members_summary"]})
    return res.get("fields", {}).get("members_summary") or []


def _interpretation(rid: str) -> str:
    res = _call("read_entity", {"entity_id": rid,
                                 "fields": ["interpretation"]})
    return res.get("fields", {}).get("interpretation") or ""


def test_updates_member_caption_not_interpretation():
    """Happy path: writes hit the per-member caption slot, NOT the
    Result-level interpretation."""
    rid, mid, _fig = _seed_result_with_figure_member()
    new = "PBMCs from patient 145, day 7 — 13 Leiden clusters annotated"
    res = _call("update_member_caption",
                {"result_id": rid, "member_id": mid, "caption": new})
    assert res.get("status") == "ok", res
    summary = _members_summary(rid)
    assert summary and summary[0]["caption"] == new, summary
    # Critical: interpretation must NOT have been touched. The live
    # bug confused these two; the regression guard pins them apart.
    assert _interpretation(rid) == "old interpretation block"


def test_broadcasts_entity_updated_so_card_refreshes(monkeypatch):
    """The Result card subscribes to entity_updated; without the
    broadcast the disk update is invisible until a full reload."""
    rid, mid, _fig = _seed_result_with_figure_member()

    seen: list = []
    from core.runtime import notifications
    monkeypatch.setattr(notifications, "broadcast",
                        lambda payload: seen.append(payload))

    res = _call("update_member_caption",
                {"result_id": rid, "member_id": mid, "caption": "new"})
    assert res.get("status") == "ok", res

    matching = [p for p in seen
                if p.get("type") == "entity_updated"
                and p.get("entity_id") == rid
                and p.get("member_id") == mid]
    assert matching, (
        f"no entity_updated broadcast for caption write. seen={seen}")


def test_rejects_wrong_type():
    """Calling on a non-Result entity reports a clean error."""
    from core.graph.entities import create_entity
    fig = create_entity(entity_type="figure", title="solo",
                        artifact_path=os.path.join(_tmp, "x.png"),
                        metadata={})
    open(os.path.join(_tmp, "x.png"), "w").write("x")
    res = _call("update_member_caption",
                {"result_id": fig, "member_id": "m1", "caption": "x"})
    assert "error" in res and "not 'result'" in res["error"], res


def test_rejects_unknown_member_id():
    """A wrong member id lists what's available so the agent can retry."""
    rid, _mid, _fig = _seed_result_with_figure_member()
    res = _call("update_member_caption",
                {"result_id": rid, "member_id": "m_does_not_exist",
                 "caption": "x"})
    assert "error" in res, res
    assert "m_does_not_exist" in res["error"], res
    # Lists the actual member ids — addressing hint, mirrors list_revisions.
    assert "m_abc" in res["error"], res


# ─── figure-id fallback (the prj_ab1b55fe friction shape) ────────────────
def _seed_result_with_revision_chain() -> tuple[str, str, list[str]]:
    """Mirror the live session: a Result whose member.ref points at the
    chain ANCHOR (oldest figure), with several revisions newer than the
    anchor. Returns (result_id, member_id, [chain_ids oldest→newest])."""
    from core.graph.entities import create_entity
    from core.graph.edges import add_edge
    chain: list[str] = []
    for i in range(4):
        p = os.path.join(_tmp, f"chain_{i}.png")
        open(p, "w").write("x")
        chain.append(create_entity(
            entity_type="figure", title="UMAP of cells",
            artifact_path=p, metadata={"thread_id": "thr_member_cap"},
        ))
    # v1 ← v2 ← v3 ← v4
    for i in range(1, len(chain)):
        add_edge(source_id=chain[i], target_id=chain[i-1],
                 rel_type="wasRevisionOf")
    rid = create_entity(
        entity_type="result", title="UMAP Result",
        metadata={"thread_id": "thr_member_cap",
                  "members": [{"id": "m_slot", "kind": "figure",
                                "ref": chain[0],            # anchor
                                "caption": "auto-caption",
                                "caption_origin": "ai"}]},
    )
    add_edge(rid, chain[0], "includes")
    return rid, "m_slot", chain


def test_resolves_latest_figure_id_to_member_slot():
    """Live friction shape (prj_ab1b55fe thr_e692a202, 2026-06-11): after
    multiple make_revision calls the agent kept the LATEST figure id in
    memory and passed it as member_id. Tool must self-heal: walk each
    member's revision chain and route to the slot if exactly one chain
    contains the supplied id."""
    rid, mid, chain = _seed_result_with_revision_chain()
    latest = chain[-1]   # v4 — what the agent thinks of as "this figure"
    res = _call("update_member_caption",
                {"result_id": rid, "member_id": latest,
                 "caption": "new caption from agent"})
    assert res.get("status") == "ok", res
    assert res.get("member_id") == mid, res
    assert res.get("resolved_via") == "figure_chain", res
    summary = _members_summary(rid)
    assert summary and summary[0]["caption"] == "new caption from agent"


def test_resolves_oldest_anchor_id_to_member_slot():
    """A figure id from anywhere in the chain — head, middle, anchor —
    resolves to the same slot. Mirrors the agent passing the displayed
    id vs. the anchor."""
    rid, mid, chain = _seed_result_with_revision_chain()
    middle = chain[1]   # v2
    res = _call("update_member_caption",
                {"result_id": rid, "member_id": middle,
                 "caption": "middle-id resolved"})
    assert res.get("status") == "ok", res
    assert res.get("member_id") == mid
    assert res.get("resolved_via") == "figure_chain"


def test_direct_member_id_keeps_resolved_via_unchanged():
    """When the agent gets it right, no fallback fires and the resolution
    path is reported as 'member_id' — useful as a tracer for telemetry."""
    rid, mid, _chain = _seed_result_with_revision_chain()
    res = _call("update_member_caption",
                {"result_id": rid, "member_id": mid, "caption": "ok"})
    assert res.get("status") == "ok"
    assert res.get("resolved_via") == "member_id"


def test_ambiguous_figure_id_refuses_with_helpful_error():
    """If the SAME figure id sits in two different members' chains (rare
    but possible — same chain referenced by two slots), refuse rather
    than guess. The error names the candidates so the agent can retry."""
    rid, _mid, chain = _seed_result_with_revision_chain()
    # Add a second member whose ref is also in the chain.
    from core.graph.entities import get_entity, update_entity
    from core.graph.edges import add_edge
    r = get_entity(rid)
    meta = dict(r.get("metadata") or {})
    meta["members"] = list(meta.get("members") or []) + [
        {"id": "m_slot2", "kind": "figure", "ref": chain[-1],
         "caption": "second slot"},
    ]
    update_entity(rid, metadata=meta)
    add_edge(rid, chain[-1], "includes")

    # chain[1] (a middle revision) is in both slots' chains now.
    middle = chain[1]
    res = _call("update_member_caption",
                {"result_id": rid, "member_id": middle, "caption": "x"})
    assert "error" in res, res
    assert "multiple" in res["error"].lower() \
        or "ambiguous" in res["error"].lower(), res
    assert "m_slot" in res["error"] and "m_slot2" in res["error"], res


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
