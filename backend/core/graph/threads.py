"""Thread (conversation-container) helpers.

`thread` is treated as an opaque entity type here; the seam-check tolerates
the literal in the bootstrap calls because thread is a conversational
container, not a bio finding. The bio-shaped open-questions / lifecycle
fields live in metadata.
"""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn
from core.graph.entities import create_entity, list_entities


def create_thread(title: str, question: str = "") -> str:
    return create_entity(
        entity_type="thread", title=title or "Untitled investigation",  # noqa: seam
        metadata={"question": question, "open_questions": [], "lifecycle": "open"},
    )


def list_threads() -> list[dict]:
    return list_entities(type_filter="thread", include_archived=False)  # noqa: seam


def find_default_thread() -> Optional[str]:
    """The project's default thread entity (metadata.is_default), or None if it
    hasn't been materialized yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, metadata FROM entities WHERE type='thread' "  # noqa: seam
            "AND deleted_at IS NULL AND status != 'archived'"
        ).fetchall()
    for r in rows:
        m = json.loads(r["metadata"]) if r["metadata"] else {}
        if m.get("is_default"):
            return r["id"]
    return None


def get_or_create_default_thread() -> str:
    """Lazily materialize the default thread into a real entity and adopt any
    previously unthreaded messages."""
    tid = find_default_thread()
    if tid:
        return tid
    tid = create_entity(
        entity_type="thread", title="Main thread",  # noqa: seam
        metadata={"question": "", "open_questions": [], "lifecycle": "open", "is_default": True},
    )
    with _conn() as c:
        c.execute("UPDATE messages SET thread_id = ? WHERE thread_id IS NULL", (tid,))
        c.commit()
    return tid
