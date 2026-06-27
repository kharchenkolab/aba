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
    from core.graph.entities import find_entities   # P3.1: store read API, not raw SQL
    rows = find_entities(type="figure", title=title, status="active",
                         descending=True, limit=1)
    return rows[0] if rows else None


def figure_history(entity_id: str, *,
                    include_superseded: bool = False) -> list[dict]:
    """Return the version chain for a figure, newest first. Follows
    wasRevisionOf edges in both directions from the given entity.

    By default, entries with status='superseded' are skipped — they're
    the displaced branches left over when a user revised from a non-
    latest revision. The visible chain stays linear. Pass
    `include_superseded=True` to get the full graph (admin/debug).

    When walking "newer" from a given entity and finding MULTIPLE
    wasRevisionOf children (a branch), we pick the most-recently-
    created ACTIVE child. Superseded children are skipped. This is
    how branching collapses back to a linear chain for display.
    """
    chain_ids: list[str] = [entity_id]
    # Older: this --wasRevisionOf--> older. There's only one parent
    # by construction (each new revision points at exactly one source).
    cur = entity_id
    while True:
        with _conn() as c:
            r = c.execute(
                "SELECT eb.target_id FROM entity_edges eb "
                "JOIN entities e ON e.id = eb.target_id "
                "WHERE eb.source_id=? AND eb.rel_type='wasRevisionOf'"
                + ("" if include_superseded
                   else " AND e.status != 'superseded'"),
                (cur,),
            ).fetchone()
        if not r:
            break
        cur = r["target_id"]
        if cur in chain_ids:
            break
        chain_ids.append(cur)
    # Newer: newer --wasRevisionOf--> this. With branching, there can
    # be multiple newers. Two modes:
    #   - default (include_superseded=False): pick the most-recently-
    #     created ACTIVE child at each step → linear "current trunk".
    #   - include_superseded=True: BFS over ALL children regardless of
    #     status → full graph for admin/debug.
    if include_superseded:
        # BFS over the whole forward graph, then order by created_at desc
        frontier = [entity_id]
        seen_newer: list[str] = []
        visited = {entity_id}
        while frontier:
            nxt: list[str] = []
            for cur in frontier:
                with _conn() as c:
                    rows = c.execute(
                        "SELECT eb.source_id, e.created_at FROM entity_edges eb "
                        "JOIN entities e ON e.id = eb.source_id "
                        "WHERE eb.target_id=? AND eb.rel_type='wasRevisionOf' "
                        "ORDER BY e.created_at DESC",
                        (cur,),
                    ).fetchall()
                for r in rows:
                    sid = r["source_id"]
                    if sid in visited:
                        continue
                    visited.add(sid)
                    seen_newer.append(sid)
                    nxt.append(sid)
            frontier = nxt
        # newest first (insert each at the front would reverse BFS order;
        # easier: collect and sort by created_at desc at the end)
        with _conn() as c:
            ts = {sid: c.execute(
                "SELECT created_at FROM entities WHERE id=?", (sid,)
            ).fetchone()["created_at"] for sid in seen_newer}
        seen_newer.sort(key=lambda i: ts.get(i, ""), reverse=True)
        chain_ids = seen_newer + chain_ids
    else:
        cur = entity_id
        while True:
            with _conn() as c:
                r = c.execute(
                    "SELECT eb.source_id FROM entity_edges eb "
                    "JOIN entities e ON e.id = eb.source_id "
                    "WHERE eb.target_id=? AND eb.rel_type='wasRevisionOf' "
                    "AND e.status != 'superseded' "
                    "ORDER BY e.created_at DESC LIMIT 1",
                    (cur,),
                ).fetchone()
            if not r:
                break
            cur = r["source_id"]
            if cur in chain_ids:
                break
            chain_ids.insert(0, cur)
    return [e for e in (get_entity(i) for i in chain_ids) if e]
