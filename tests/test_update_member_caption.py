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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
