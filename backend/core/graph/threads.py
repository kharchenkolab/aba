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
from core.graph.entities import create_entity, list_entities, get_entity, update_entity


def create_thread(title: str, question: str = "",
                  spec: Optional[str] = None) -> str:
    """Create a new thread entity. `spec` (optional) pins this thread
    to a specific primary AgentSpec — overrides ABA_PRIMARY_SPEC and
    the "guide" default for any turn served on this thread. Useful
    for "new chat with lean backend" without flipping the install-wide
    env var."""
    md: dict = {"question": question, "open_questions": [], "lifecycle": "open"}
    if spec:
        md["spec"] = spec
    from core.graph.derivation import manual
    return create_entity(
        entity_type="thread", title=title or "Untitled investigation",  # noqa: seam
        derivation=manual(),   # Phase 2B (actor from ambient: human via route / agent via turn)
        metadata=md,
    )


def get_thread_spec(thread_id: str) -> Optional[str]:
    """Read the spec pinned on a thread (if any). Returns None when
    the thread doesn't exist OR has no spec set — callers fall back to
    the env override / "guide" default."""
    ent = get_entity(thread_id)
    if not ent:
        return None
    return (ent.get("metadata") or {}).get("spec")


def set_thread_spec(thread_id: str, spec: Optional[str]) -> None:
    """Pin or clear the spec for a thread. Passing None / empty string
    clears it (the thread reverts to env/default resolution)."""
    ent = get_entity(thread_id)
    if not ent:
        return
    md = dict(ent.get("metadata") or {})
    if spec:
        md["spec"] = spec
    else:
        md.pop("spec", None)
    update_entity(thread_id, metadata=md)


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
    from core.graph.derivation import manual, SYSTEM_ACTOR
    tid = create_entity(
        entity_type="thread", title="Main thread",  # noqa: seam
        derivation=manual(), actor=SYSTEM_ACTOR,   # Phase 2B: system-created default
        metadata={"question": "", "open_questions": [], "lifecycle": "open", "is_default": True},
    )
    with _conn() as c:
        c.execute("UPDATE messages SET thread_id = ? WHERE thread_id IS NULL", (tid,))
        c.commit()
    return tid
