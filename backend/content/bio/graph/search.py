"""Faceted search across entities, files, and messages. Bio because it filters
out workspace + bio entity types, walks the bio virtual files tree, and shapes
message snippets the way the bio UI expects."""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn


def _walk_files(node: dict, ql: str, out: list, seen: set, limit: int) -> None:
    """Collect file/readme nodes whose name, path, or title matches `ql`
    (case-insensitive substring). Dedupes by path (the tree is multi-rooted —
    the same artifact can appear under several paths; we keep distinct paths)."""
    if len(out) >= limit:
        return
    if node.get("kind") in ("file", "readme"):
        name = node.get("name") or ""
        path = node.get("path") or ""
        title = node.get("title") or ""
        if ql in name.lower() or ql in path.lower() or ql in title.lower():
            if path and path not in seen:
                seen.add(path)
                out.append({
                    "path": path, "name": name, "kind": node.get("kind"),
                    "entity_id": node.get("entity_id"),
                    "entity_type": node.get("entity_type"),
                    "title": title or name,
                })
    for ch in (node.get("children") or []):
        _walk_files(ch, ql, out, seen, limit)


def search(q: str, limit: int = 25) -> dict:
    """Lexical search (LIKE) across entity titles/notes, the virtual files tree
    (by name/path), and message text. FTS/semantic + file-content can replace
    later; the response shape (entities/files/messages) stays stable."""
    q = q.strip()
    if not q:
        return {"entities": [], "files": [], "messages": []}
    like = f"%{q}%"
    ql = q.lower()
    with _conn() as c:
        ent_rows = c.execute(
            "SELECT id, type, title, status, created_at FROM entities "
            "WHERE deleted_at IS NULL AND status = 'active' AND type != 'workspace' "
            "AND (title LIKE ? OR notes LIKE ?) "
            # Rank title matches above notes-only matches; recency breaks ties.
            "ORDER BY (CASE WHEN title LIKE ? THEN 0 ELSE 1 END), updated_at DESC LIMIT ?",
            (like, like, like, limit),
        ).fetchall()
        msg_rows = c.execute(
            "SELECT id, role, content, ts, thread_id FROM messages WHERE content LIKE ? "
            "ORDER BY id DESC LIMIT ?",
            (like, limit),
        ).fetchall()
    entities = [
        {"id": r["id"], "type": r["type"], "title": r["title"],
         "status": r["status"], "created_at": r["created_at"]}
        for r in ent_rows
    ]

    # Files — walk the bio virtual files tree (the same source the Files tab
    # renders), so user-visible files are searchable even though they aren't
    # DB entities. Best-effort: never let a tree-build hiccup fail the search.
    files: list[dict] = []
    try:
        from content.bio.files.tree import build_files_tree
        root = build_files_tree(include_archived=False)
        _walk_files(root, ql, files, set(), limit)
    except Exception:
        files = []

    messages = []
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
        messages.append({"id": r["id"], "role": r["role"], "ts": r["ts"],
                         "thread_id": r["thread_id"], "snippet": snippet})
    return {"entities": entities, "files": files, "messages": messages}


def find_kept_note(source_key: str) -> Optional[str]:
    """Return the id of an active kept-note snapshot for this message key.
    Bio because 'note' is a bio entity type."""
    from core.graph.entities import find_entities   # P3.1: store read API, not raw SQL
    rows = find_entities(type="note", status="active", not_deleted=True,
                         metadata_contains={"source_key": source_key})
    return rows[0]["id"] if rows else None
