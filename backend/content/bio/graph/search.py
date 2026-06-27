"""Faceted search across entities and messages. Bio because it filters
out workspace + bio entity types and shapes message snippets the way
the bio UI expects."""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn


def search(q: str, limit: int = 25) -> dict:
    """Lexical search (LIKE) across entity titles/notes and message text.
    FTS/semantic can replace later."""
    q = q.strip()
    if not q:
        return {"entities": [], "messages": []}
    like = f"%{q}%"
    with _conn() as c:
        ent_rows = c.execute(
            "SELECT id, type, title, status, created_at FROM entities "
            "WHERE deleted_at IS NULL AND status = 'active' AND type != 'workspace' "
            "AND (title LIKE ? OR notes LIKE ?) ORDER BY updated_at DESC LIMIT ?",
            (like, like, limit),
        ).fetchall()
        msg_rows = c.execute(
            "SELECT id, role, content, ts FROM messages WHERE content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (like, limit),
        ).fetchall()
    entities = [
        {"id": r["id"], "type": r["type"], "title": r["title"],
         "status": r["status"], "created_at": r["created_at"]}
        for r in ent_rows
    ]
    messages = []
    ql = q.lower()
    for r in msg_rows:
        text = ""
        try:
            for b in json.loads(r["content"]):
                if isinstance(b, dict) and b.get("type") == "text":
                    text += (b.get("text") or "") + " "
        except Exception:
            text = r["content"]
        idx = text.lower().find(ql)
        if idx < 0:
            continue
        start = max(0, idx - 40)
        snippet = ("…" if start else "") + text[start:idx + len(q) + 60].strip() + "…"
        messages.append({"id": r["id"], "role": r["role"], "ts": r["ts"], "snippet": snippet})
    return {"entities": entities, "messages": messages}


def find_kept_note(source_key: str) -> Optional[str]:
    """Return the id of an active kept-note snapshot for this message key.
    Bio because 'note' is a bio entity type."""
    from core.graph.entities import find_entities   # P3.1: store read API, not raw SQL
    rows = find_entities(type="note", status="active", not_deleted=True,
                         metadata_contains={"source_key": source_key})
    return rows[0]["id"] if rows else None
