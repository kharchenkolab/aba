"""Typed entity edges (W3C PROV-O + ABA extensions). Domain-neutral."""
from __future__ import annotations
import json
import logging
from typing import Optional

from core.graph._schema import _conn, _utcnow

_log = logging.getLogger(__name__)


def _edge_validate(source_id: str, target_id: str, rel_type: str) -> None:
    """WU-2 (post-Phase-4.5): HARD-REJECT edges that don't match the
    registry's allowed_edges. Unknown types skip validation (legacy
    data, synthetic test types). The lookup costs one tiny SELECT for
    the source/target types — only fires inside add_edge, bounded
    cost. Raises ValueError on a real violation; the bio router
    converts ValueError → HTTP 422 at the boundary."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT id, type FROM entities WHERE id IN (?, ?)",
                (source_id, target_id),
            ).fetchall()
        types = {r["id"]: r["type"] for r in rows}
        s = types.get(source_id)
        t = types.get(target_id)
        if not s or not t:
            return  # one endpoint missing — let SQL handle it
        from core.entity_types import check_edge
        msgs = check_edge(s, t, rel_type)
    except Exception:  # noqa: BLE001 — registry import failure ≠ data violation
        msgs = []
    if msgs:
        # WU-2: hard-reject — was warning-only through Phase 4.5;
        # p10 enforces every add_edge() call site has a declared
        # (src, tgt, rel) triple. A new violation here means either
        # a YAML gap or a buggy edge — both deserve loud failure.
        raise ValueError("entity_types: " + "; ".join(msgs))


def add_edge(source_id: str, target_id: str, rel_type: str,
             metadata: Optional[dict] = None) -> None:
    """Insert an edge; idempotent via UNIQUE(source, target, rel)."""
    _edge_validate(source_id, target_id, rel_type)
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO entity_edges "
            "(source_id, target_id, rel_type, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_id, target_id, rel_type,
             json.dumps(metadata) if metadata else None, now),
        )
        c.commit()
        inserted = cur.rowcount > 0
    # Only emit if a row actually landed (INSERT OR IGNORE may dedupe).
    if inserted:
        _emit_edge_op("add", source_id, target_id, rel_type, metadata)


def remove_edge(source_id: str, target_id: str, rel_type: str) -> None:
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM entity_edges WHERE source_id = ? AND target_id = ? AND rel_type = ?",
            (source_id, target_id, rel_type),
        )
        c.commit()
        removed = cur.rowcount > 0
    if removed:
        _emit_edge_op("remove", source_id, target_id, rel_type, None)


def edges_from(source_id: str) -> list[dict]:
    """Outgoing edges (this entity points to others)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entity_edges WHERE source_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
    return [
        {
            "source_id": r["source_id"],
            "target_id": r["target_id"],
            "rel_type": r["rel_type"],
            "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def edges_to(target_id: str) -> list[dict]:
    """Incoming edges (others point at this entity)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entity_edges WHERE target_id = ? ORDER BY id",
            (target_id,),
        ).fetchall()
    return [
        {
            "source_id": r["source_id"],
            "target_id": r["target_id"],
            "rel_type": r["rel_type"],
            "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# ─── Recovery archive emit ────────────────────────────────────────────────
def _emit_edge_op(op: str, src: str, dst: str, rel: str, meta: Optional[dict]) -> None:
    """Best-effort scribe enqueue: append the edge op to the FS recovery
    archive's edges.jsonl. Failures swallowed — the DB write succeeded."""
    try:
        from core.recovery import get_scribe, EdgeOp        # noqa: PLC0415
        from core.config import current_project_id          # noqa: PLC0415
        get_scribe().enqueue(EdgeOp(
            pid=current_project_id(),
            op=op, src=src, dst=dst, rel=rel, meta=meta,
        ))
    except Exception:
        _log.debug("scribe emit_edge_op failed (%s %s→%s %s)", op, src, dst, rel, exc_info=True)
