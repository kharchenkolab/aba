"""Message log (per-entity, per-thread). Domain-neutral."""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn, _utcnow, WORKSPACE_ID


def append_message(
    role: str,
    content_blocks: list,
    *,
    entity_id: str = WORKSPACE_ID,
    focus_entity_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> int:
    """Append a message to the project conversation.

    `focus_entity_id` records which entity the user was looking at when this
    message was sent. `thread_id` is the line of inquiry it belongs to;
    NULL means the default thread."""
    ts = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (entity_id, focus_entity_id, thread_id, role, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entity_id, focus_entity_id, thread_id, role, json.dumps(content_blocks), ts),
        )
        c.commit()
        mid = cur.lastrowid
    _emit_message_appended({
        "id": mid, "entity_id": entity_id, "focus_entity_id": focus_entity_id,
        "thread_id": thread_id, "role": role, "content": content_blocks, "ts": ts,
    })
    return mid


def get_messages(entity_id: str = WORKSPACE_ID, thread_id: Optional[str] = None) -> list[dict]:
    """Conversation for a project. `thread_id`:
      - None        → all messages (default; preserves single-conversation reads);
      - "default"   → only the default thread (thread_id IS NULL);
      - "<thr_id>"  → that specific thread.
    """
    where = "entity_id = ?"
    params: list = [entity_id]
    if thread_id == "default":
        where += " AND thread_id IS NULL"
    elif thread_id is not None:
        where += " AND thread_id = ?"
        params.append(thread_id)
    with _conn() as c:
        rows = c.execute(
            f"SELECT role, content, ts, focus_entity_id, thread_id FROM messages "
            f"WHERE {where} ORDER BY id",
            params,
        ).fetchall()
    return [
        {
            "role": r["role"],
            "content": json.loads(r["content"]),
            "ts": r["ts"],
            "focus_entity_id": r["focus_entity_id"],
            "thread_id": r["thread_id"],
        }
        for r in rows
    ]


def clear_messages(entity_id: str = WORKSPACE_ID):
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE entity_id = ?", (entity_id,))
        c.commit()
    _emit_messages_cleared(entity_id, thread_id=None)


def get_all_messages():
    """Legacy: returns workspace-scoped messages."""
    return get_messages(WORKSPACE_ID)


def clear_history():
    """Legacy: clears workspace-scoped messages only."""
    clear_messages(WORKSPACE_ID)


# ─── Recovery archive emit ────────────────────────────────────────────────
def _emit_message_appended(row: dict) -> None:
    try:
        from core.recovery import get_scribe, MessageAppended  # noqa: PLC0415
        from core.config import current_project_id             # noqa: PLC0415
        get_scribe().enqueue(MessageAppended(pid=current_project_id(), row=row))
    except Exception:
        pass


def _emit_messages_cleared(entity_id: str, thread_id: Optional[str]) -> None:
    try:
        from core.recovery import get_scribe, MessagesCleared  # noqa: PLC0415
        from core.config import current_project_id             # noqa: PLC0415
        get_scribe().enqueue(MessagesCleared(
            pid=current_project_id(),
            entity_id=entity_id,
            thread_id=thread_id,
        ))
    except Exception:
        pass
