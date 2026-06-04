"""Recompute + persist display_path for entities (files.md F3).

display_path is derived (from title + conventions), but persisted so:
  - /api/files/tree avoids a Python call per row,
  - F4 download/materialize can do a single SQL query by path prefix,
  - the lab research repo can reconstruct paths even if conventions drift.

The recompute helper is called:
  - by content/bio/lifecycle/registry.py after an artifact registers,
  - by main.entities_patch after a title change,
  - by the startup backfill in main.startup.
"""
from __future__ import annotations
from typing import Iterable, Optional

from core.files.registry import display_path_for
from core.graph.entities import get_entity, update_entity, list_entities


def recompute_display_path(entity_id: str) -> Optional[str]:
    """Compute and persist display_path. Returns the new value, or None
    if the entity doesn't exist."""
    e = get_entity(entity_id)
    if not e:
        return None
    dp = display_path_for(e)
    if dp == e.get("display_path"):
        return dp  # no-op; spare a write
    update_entity(entity_id, display_path=dp)
    return dp


def backfill_missing_display_paths() -> int:
    """One-shot pass at startup. Returns the number of rows touched."""
    n = 0
    for e in list_entities(include_archived=True):
        if e.get("display_path"):
            continue
        if e.get("type") == "workspace":
            continue
        dp = display_path_for(e)
        update_entity(e["id"], display_path=dp)
        n += 1
    return n


def _on_project_open(ctx: dict) -> None:
    """Hook handler: backfill display_path on every project switch.
    Cheap + idempotent. Registered with core.hooks.dispatcher from
    content/bio/__init__.py (Phase C.4 of misc/modularity_audit.md)."""
    backfill_missing_display_paths()


# Self-register at module import time; the bio package __init__ imports
# this module via .files.layout which doesn't pull display.py directly,
# so registration is wired explicitly from content/bio/__init__.py.


def recompute_many(entity_ids: Iterable[str]) -> int:
    n = 0
    for eid in entity_ids:
        if recompute_display_path(eid):
            n += 1
    return n
