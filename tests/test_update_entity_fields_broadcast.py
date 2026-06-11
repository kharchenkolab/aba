"""update_entity_fields must broadcast entity_updated.

Live bug (prj_128380fd thr_deed230d, 2026-06-11): the agent called
update_entity_fields to update a Result's `interpretation` metadata.
The disk write succeeded, but the Result card kept showing the old
caption — the frontend never got a refresh signal because the tool
didn't emit a notification. Typed siblings (promote_to_result,
make_revision) DO broadcast; the generic update was missed.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


_tmp = tempfile.mkdtemp(prefix="aba_uef_")
_db = os.path.join(_tmp, "uef.db")
# Use direct assignment (not setdefault) — the env may already have a
# read-only ABA_RUNTIME_DIR from a parent shell; our tempdir must win.
os.environ["ABA_DB_PATH"] = _db
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"] = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"] = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"] = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db   # noqa: E402
init_db()

import content.bio    # noqa: E402,F401  — registers tools

# d8_entity_ops.py pattern: register the in-process MCP server so the
# agent's tool dispatcher knows about update_entity_fields and the rest
# of the aba_core surface.
from core.runtime.mcp import register_inprocess_server, _reset_for_testing   # noqa: E402
from content.bio.mcp_servers.aba_core import make_server                       # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)


def _make_result_entity() -> str:
    from core.graph.entities import create_entity
    return create_entity(
        entity_type="result",
        title="UMAP test result",
        metadata={"interpretation": "old caption"},
    )


def _call_update_impl(eid: str, fields: dict) -> dict:
    """Drive the agent's tool path via execute_tool — same dispatcher
    d8_entity_ops.py uses. Sync, no async plumbing."""
    import json
    from content.bio.tools import execute_tool
    raw = execute_tool("update_entity_fields",
                       {"entity_id": eid, "fields": fields},
                       {"thread_id": "default"})
    return json.loads(raw) if isinstance(raw, str) else raw


def test_update_entity_fields_broadcasts_entity_updated(monkeypatch):
    """The tool's notification path must reach the frontend's refresh hook."""
    seen: list = []
    # The tool imports broadcast lazily inside the call path. Pre-bind the
    # spy on the module so the import resolves to our recorder.
    from core.runtime import notifications
    monkeypatch.setattr(notifications, "broadcast",
                        lambda payload: seen.append(payload))

    eid = _make_result_entity()
    res = _call_update_impl(eid, {"interpretation": "new caption"})
    # The tool returned ok
    assert res.get("status") == "ok", res
    # And the broadcast happened with the right shape — entity_updated +
    # the entity_id so the frontend can target the right card refresh.
    matching = [p for p in seen
                if p.get("type") == "entity_updated"
                and p.get("entity_id") == eid]
    assert matching, (
        f"no entity_updated broadcast emitted by update_entity_fields. "
        f"Broadcasts seen: {seen}")


def test_no_broadcast_when_no_actual_change(monkeypatch):
    """If the agent tried to set fields to None (PATCH no-op semantics) and
    nothing actually changed, don't fire a spurious refresh."""
    seen: list = []
    from core.runtime import notifications
    monkeypatch.setattr(notifications, "broadcast",
                        lambda payload: seen.append(payload))

    eid = _make_result_entity()
    # All top-level keys are None → polite no-op per the tool contract.
    res = _call_update_impl(eid, {"title": None})
    assert res.get("status") == "ok"
    matching = [p for p in seen
                if p.get("type") == "entity_updated"
                and p.get("entity_id") == eid]
    assert not matching, (
        f"unexpected broadcast on no-op update: {matching}")
