"""Append-only event log, advisor-notes, context-assemblies, suggestions.

Domain-neutral logging primitives. `add_advisor_note` needs the entity
title for a friendly event payload; it does a deferred import of
get_entity to avoid a circular import with core.graph.entities."""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn, _utcnow


def log_event(kind: str, entity_id: Optional[str] = None,
              title: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """Append an event to the activity/audit log. Best-effort; never raises."""
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO events (kind, entity_id, title, detail, ts) VALUES (?, ?, ?, ?, ?)",
                (kind, entity_id, title, json.dumps(detail) if detail else None, _utcnow()),
            )
            c.commit()
    except Exception:
        pass


def list_events(limit: int = 50, offset: int = 0) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return [
        {
            "id": r["id"], "kind": r["kind"], "entity_id": r["entity_id"],
            "title": r["title"], "detail": json.loads(r["detail"]) if r["detail"] else None,
            "ts": r["ts"],
        }
        for r in rows
    ]


def add_advisor_note(entity_id: str, advisor: str, text: str,
                     metadata: Optional[dict] = None) -> int:
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO advisor_notes (entity_id, advisor, text, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (entity_id, advisor, text,
             json.dumps(metadata) if metadata else None, now),
        )
        c.commit()
        note_id = cur.lastrowid
    # Deferred import to break the circular core.graph.audit ↔ entities
    # dependency at module-load time.
    from core.graph.entities import get_entity
    ent = get_entity(entity_id)
    log_event("advisor_note", entity_id=entity_id,
              title=(ent["title"] if ent else None), detail={"advisor": advisor})
    return note_id


def set_advisor_note_status(note_id: int, status: str) -> bool:
    """Mark a note 'tried'/'dismissed' so it stops surfacing as a fresh idea."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE advisor_notes SET status = ? WHERE id = ?", (status, note_id)
        )
        return cur.rowcount > 0


def list_advisor_notes(entity_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT n.id, n.entity_id, n.advisor, n.text, n.metadata, n.status, "
            "       n.created_at, e.type AS e_type, e.title AS e_title "
            "FROM advisor_notes n LEFT JOIN entities e ON e.id = n.entity_id "
            "WHERE n.entity_id = ? AND n.status = 'active' "
            "ORDER BY n.id",
            (entity_id,),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "entity_id": r["entity_id"],
            "advisor": r["advisor"],
            "text": r["text"],
            "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
            "status": r["status"],
            "created_at": r["created_at"],
            "entity_type": r["e_type"],
            "entity_title": r["e_title"],
        }
        for r in rows
    ]


def log_context_assembly(
    session_id: str,
    turn_index: int,
    focus_entity_id: Optional[str],
    focus_entity_type: Optional[str],
    fields_preloaded: list[str],
    tool_calls: list[str],
    turn_text_len: int,
    manifest: Optional[dict] = None,
) -> int:
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO context_assemblies "
            "(session_id, turn_index, focus_entity_id, focus_entity_type, "
            " fields_preloaded, tool_calls, n_tool_calls, turn_text_len, "
            " manifest_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, turn_index, focus_entity_id, focus_entity_type,
                json.dumps(fields_preloaded), json.dumps(tool_calls),
                len(tool_calls), turn_text_len,
                json.dumps(manifest) if manifest is not None else None,
                now,
            ),
        )
        c.commit()
        return cur.lastrowid


def latest_manifest_for_thread(thread_id: Optional[str]) -> Optional[dict]:
    """Drawer fallback (T2.4): return the most-recent persisted Manifest
    snapshot whose focus was inside this thread. Returns None if no
    manifest has been logged yet."""
    if not thread_id:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT manifest_json FROM context_assemblies "
            "WHERE manifest_json IS NOT NULL "
            "ORDER BY id DESC LIMIT 50"
        ).fetchall()
    for r in row:
        try:
            m = json.loads(r["manifest_json"])
        except Exception:
            continue
        if (m.get("thread") or {}).get("thread_id") == thread_id:
            return m
    # Fall back to most recent overall
    return json.loads(row[0]["manifest_json"]) if row else None


def session_assembly_summary(session_id: str) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n_turns, SUM(n_tool_calls) AS total_tool_calls, "
            "MAX(turn_index) AS last_turn FROM context_assemblies "
            "WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return {
        "session_id": session_id,
        "n_turns": row["n_turns"] or 0,
        "total_tool_calls": row["total_tool_calls"] or 0,
        "last_turn": row["last_turn"],
    }


def add_context_suggestion(
    session_id: str,
    entity_type: Optional[str],
    trigger: str,
    suggestion: str,
) -> int:
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO context_suggestions "
            "(session_id, entity_type, trigger, suggestion, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, entity_type, trigger, suggestion, now),
        )
        c.commit()
        return cur.lastrowid


def list_context_suggestions(
    status: Optional[str] = "pending",
    max_age_days: Optional[int] = 14,
) -> list[dict]:
    """List suggestions, optionally scoped to a status.

    `max_age_days`: items older than this many days are excluded by
    default (so the drawer / rail badge silently auto-stale once they
    age out, avoiding the 'two-week-old reflection sitting unactioned
    forever' pattern). Pass None to include every age.
    """
    import datetime as _dt
    q = "SELECT * FROM context_suggestions"
    clauses: list[str] = []
    args: list = []
    if status:
        clauses.append("status = ?"); args.append(status)
    if max_age_days is not None:
        cutoff = (_dt.datetime.now(_dt.timezone.utc)
                  - _dt.timedelta(days=max_age_days)).isoformat()
        clauses.append("created_at >= ?"); args.append(cutoff)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY id DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [
        {
            "id": r["id"],
            "session_id": r["session_id"],
            "entity_type": r["entity_type"],
            "trigger": r["trigger"],
            "suggestion": r["suggestion"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def update_context_suggestion_status(suggestion_id: int, status: str) -> bool:
    with _conn() as c:
        cur = c.execute(
            "UPDATE context_suggestions SET status = ? WHERE id = ?",
            (status, suggestion_id),
        )
        c.commit()
        return cur.rowcount > 0


def reject_all_pending_suggestions() -> int:
    """Bulk 'reject all pending' — flips every pending row to rejected.
    Returns the count touched. Used by the drawer's 'Reject all' button
    when the user wants to wipe the queue without per-row clicking."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE context_suggestions SET status = 'rejected' WHERE status = 'pending'"
        )
        c.commit()
        return cur.rowcount or 0
