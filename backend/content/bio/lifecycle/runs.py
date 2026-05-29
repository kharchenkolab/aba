"""Analysis-run lifecycle (entity-model v3 'Analysis run').

A Run groups the outputs of a coherent, usually planned, analysis so they read
as one unit instead of scattering across per-turn `analysis` entities. A Run IS
an `analysis` entity (files/tree.py RUN_TYPES = {"analysis"}) tagged in metadata:

    thread_id:  <home thread>     — so the Files tree places it under the thread
    run_state:  "open" | "closed"

At most one Run is *open* per thread — the "active" one. While it's open,
harvested artifacts (lifecycle/registry._ensure_analysis) attach to it, so a
multi-turn pipeline is one Run with its figures/tables rather than a pile of
per-turn analyses.

Opened by the agent (open_run) as it begins executing an approved plan, or
rotated by the next open_run. Closed by close_run (explicit, or on a topic
pivot the agent recognizes). Closing an EMPTY Run discards it, so an abandoned
or re-planned analysis doesn't litter the tree.
"""
from __future__ import annotations
from typing import Optional

from core.graph._schema import _conn, WORKSPACE_ID
from core.graph.entities import (
    create_entity, get_entity, update_entity, archive_entity, list_entities,
)


def active_run_id(thread_id: str) -> Optional[str]:
    """The currently-open Run for this thread, or None (newest wins)."""
    if not thread_id:
        return None
    # list_entities orders by created_at asc → reverse for newest-first.
    for e in reversed(list_entities(type_filter="analysis", include_archived=False)):
        md = e.get("metadata") or {}
        if md.get("thread_id") == thread_id and md.get("run_state") == "open":
            return e["id"]
    return None


def _has_children(run_id: str) -> bool:
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM entities WHERE parent_entity_id = ? AND status != 'archived' LIMIT 1",
            (run_id,),
        ).fetchone()
    return r is not None


def open_run(thread_id: str, title: str, *, focus_entity_id: Optional[str] = None,
             plan_entity_id: Optional[str] = None) -> str:
    """Open a Run for the thread, rotating out any currently-open one first
    (a new boundary supersedes the previous Run). Returns the run (analysis) id."""
    close_run(thread_id)
    md: dict = {"thread_id": thread_id, "run_state": "open", "origin": "internal"}
    if plan_entity_id:
        md["plan_entity_id"] = plan_entity_id
    return create_entity(
        entity_type="analysis",
        title=(title or "Analysis run").strip()[:120],
        parent_entity_id=focus_entity_id or WORKSPACE_ID,
        metadata=md,
    )


def close_run(thread_id: str) -> Optional[str]:
    """Close the thread's open Run, if any. An EMPTY Run (no outputs, no
    captured code) is discarded instead of kept, so abandoned/re-planned
    analyses don't litter the tree. Returns the closed/discarded id, or None."""
    rid = active_run_id(thread_id)
    if not rid:
        return None
    ent = get_entity(rid)
    if not (ent or {}).get("producing_code") and not _has_children(rid):
        archive_entity(rid)
        return rid
    md = dict((ent or {}).get("metadata") or {})
    md["run_state"] = "closed"
    update_entity(rid, metadata=md)
    return rid


def append_run_code(run_id: str, code: str) -> None:
    """Accumulate a cell onto the Run's producing_code — so the Run is the
    recompute/branch unit, not just a folder of figures."""
    if not run_id or not code:
        return
    ent = get_entity(run_id)
    if not ent:
        return
    block = code.strip()
    prior = ent.get("producing_code") or ""
    if not block or block in prior:
        return
    combined = (prior + "\n\n# ---\n" + block) if prior else block
    update_entity(run_id, producing_code=combined[:20000])
