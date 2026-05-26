"""Figure version chains via the wasRevisionOf edge type.

Bio because it knows 'figure' is a type and 'wasRevisionOf' is the
revision relation. The title-based auto-supersession lookup
(`find_active_figure_by_title`) is kept for backwards-compat: it is no
longer called by the registry (commit c99594a stopped that), but other
callers may still reach for it.
"""
from __future__ import annotations
from typing import Optional

from core.graph._schema import _conn
from core.graph.entities import _row_to_entity, get_entity


def find_active_figure_by_title(title: str) -> Optional[dict]:
    """Most-recent active figure with this exact title (for version chains).
    Note: title-based auto-supersession was disabled in commit c99594a;
    this helper is kept for explicit-lookup callers only."""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM entities WHERE type='figure' AND title=? "
            "AND status='active' ORDER BY created_at DESC LIMIT 1",
            (title,),
        ).fetchone()
        return _row_to_entity(r) if r else None


def figure_history(entity_id: str) -> list[dict]:
    """Return the version chain for a figure, newest first. Follows
    wasRevisionOf edges in both directions from the given entity."""
    chain_ids: list[str] = [entity_id]
    # Older: this --wasRevisionOf--> older
    cur = entity_id
    while True:
        with _conn() as c:
            r = c.execute(
                "SELECT target_id FROM entity_edges WHERE source_id=? AND rel_type='wasRevisionOf'",
                (cur,),
            ).fetchone()
        if not r:
            break
        cur = r["target_id"]
        if cur in chain_ids:
            break
        chain_ids.append(cur)
    # Newer: newer --wasRevisionOf--> this
    cur = entity_id
    while True:
        with _conn() as c:
            r = c.execute(
                "SELECT source_id FROM entity_edges WHERE target_id=? AND rel_type='wasRevisionOf'",
                (cur,),
            ).fetchone()
        if not r:
            break
        cur = r["source_id"]
        if cur in chain_ids:
            break
        chain_ids.insert(0, cur)
    return [e for e in (get_entity(i) for i in chain_ids) if e]
