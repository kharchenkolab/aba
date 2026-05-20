import sqlite3
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("ABA_DB_PATH") or (Path(__file__).parent / "aba.db"))

# Root entity that hosts any chat not yet scoped to a specific entity.
WORKSPACE_ID = "workspace"


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _column_exists(c: sqlite3.Connection, table: str, col: str) -> bool:
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def init_db():
    """Create tables, run idempotent migrations, ensure workspace entity exists."""
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id       TEXT    NOT NULL DEFAULT 'workspace',
                focus_entity_id TEXT,
                role            TEXT    NOT NULL,
                content         TEXT    NOT NULL,
                ts              TEXT    NOT NULL
            )
        """)
        if not _column_exists(c, "messages", "entity_id"):
            c.execute("ALTER TABLE messages ADD COLUMN entity_id TEXT NOT NULL DEFAULT 'workspace'")
        if not _column_exists(c, "messages", "focus_entity_id"):
            c.execute("ALTER TABLE messages ADD COLUMN focus_entity_id TEXT")

        c.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id                TEXT PRIMARY KEY,
                type              TEXT NOT NULL,
                title             TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'active',
                artifact_path     TEXT,
                producing_code    TEXT,
                producing_params  TEXT,
                parent_entity_id  TEXT,
                scenario_of       TEXT,
                metadata          TEXT,
                tags              TEXT,
                notes             TEXT,
                pinned            INTEGER NOT NULL DEFAULT 0,
                deleted_at        TEXT,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            )
        """)
        # Idempotent additive migrations for installs predating these columns.
        for col, ddl in (
            ("tags",        "ALTER TABLE entities ADD COLUMN tags TEXT"),
            ("notes",       "ALTER TABLE entities ADD COLUMN notes TEXT"),
            ("pinned",      "ALTER TABLE entities ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
            ("deleted_at",  "ALTER TABLE entities ADD COLUMN deleted_at TEXT"),
        ):
            if not _column_exists(c, "entities", col):
                c.execute(ddl)

        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_entity_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_pinned ON entities(pinned)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_entity ON messages(entity_id)")

        # Entity edges — typed relationships between entities. W3C PROV-O
        # vocabulary (wasGeneratedBy, wasDerivedFrom, used, wasAssociatedWith)
        # plus ABA extensions (supports, weakens, variantOf, partOf).
        c.execute("""
            CREATE TABLE IF NOT EXISTS entity_edges (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id   TEXT NOT NULL,
                target_id   TEXT NOT NULL,
                rel_type    TEXT NOT NULL,
                metadata    TEXT,
                created_at  TEXT NOT NULL,
                UNIQUE(source_id, target_id, rel_type)
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_edges_source ON entity_edges(source_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_edges_target ON entity_edges(target_id)")

        # Advisor notes — agent-authored remarks attached to a focused entity.
        # See aba_arch2.md §2.4: Methodologist, Skeptic, Explorer, Stylist.
        c.execute("""
            CREATE TABLE IF NOT EXISTS advisor_notes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id    TEXT NOT NULL,
                advisor      TEXT NOT NULL,
                text         TEXT NOT NULL,
                metadata     TEXT,
                status       TEXT NOT NULL DEFAULT 'active',
                created_at   TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_entity ON advisor_notes(entity_id)")

        # Context assemblies — one row per Guide turn, capturing what was
        # loaded into context and how much extra retrieval the agent had
        # to do mid-session. Feeds the §3.6 reflection loop.
        c.execute("""
            CREATE TABLE IF NOT EXISTS context_assemblies (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id              TEXT,
                turn_index              INTEGER,
                focus_entity_id         TEXT,
                focus_entity_type       TEXT,
                fields_preloaded        TEXT,
                tool_calls              TEXT,
                n_tool_calls            INTEGER NOT NULL DEFAULT 0,
                turn_text_len           INTEGER NOT NULL DEFAULT 0,
                created_at              TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_assemblies_session ON context_assemblies(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_assemblies_focus  ON context_assemblies(focus_entity_id)")

        # Suggestions surfaced from end-of-session reflection (and probes,
        # eventually). Reviewable in the settings page; once approved,
        # appended to a per-entity-type policy file.
        c.execute("""
            CREATE TABLE IF NOT EXISTS context_suggestions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT,
                entity_type  TEXT,
                trigger      TEXT,
                suggestion   TEXT NOT NULL,
                status       TEXT NOT NULL DEFAULT 'pending',
                created_at   TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_sugg_status ON context_suggestions(status)")

        # Bootstrap workspace entity.
        row = c.execute("SELECT id FROM entities WHERE id = ?", (WORKSPACE_ID,)).fetchone()
        if not row:
            now = _utcnow()
            c.execute(
                "INSERT INTO entities (id, type, title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (WORKSPACE_ID, "workspace", "Workspace", "active", now, now),
            )
        c.commit()


# ---------- Entities ----------

def gen_entity_id(prefix: str = "ent") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def create_entity(
    *,
    entity_type: str,
    title: str,
    artifact_path: Optional[str] = None,
    producing_code: Optional[str] = None,
    producing_params: Optional[dict] = None,
    parent_entity_id: Optional[str] = None,
    scenario_of: Optional[str] = None,
    metadata: Optional[dict] = None,
    entity_id: Optional[str] = None,
) -> str:
    eid = entity_id or gen_entity_id(prefix=entity_type[:3])
    now = _utcnow()
    with _conn() as c:
        c.execute(
            """INSERT INTO entities
               (id, type, title, status, artifact_path, producing_code,
                producing_params, parent_entity_id, scenario_of, metadata,
                created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, entity_type, title, artifact_path, producing_code,
                json.dumps(producing_params) if producing_params else None,
                parent_entity_id, scenario_of,
                json.dumps(metadata) if metadata else None,
                now, now,
            ),
        )
        c.commit()
    return eid


def _row_to_entity(r) -> dict:
    return {
        "id": r["id"],
        "type": r["type"],
        "title": r["title"],
        "status": r["status"],
        "artifact_path": r["artifact_path"],
        "producing_code": r["producing_code"],
        "producing_params": json.loads(r["producing_params"]) if r["producing_params"] else None,
        "parent_entity_id": r["parent_entity_id"],
        "scenario_of": r["scenario_of"],
        "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
        "tags": json.loads(r["tags"]) if r["tags"] else [],
        "notes": r["notes"],
        "pinned": bool(r["pinned"]) if r["pinned"] is not None else False,
        "deleted_at": r["deleted_at"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def get_entity(entity_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return _row_to_entity(r) if r else None


def list_entities(
    *,
    exclude_workspace: bool = False,
    include_archived: bool = True,
    type_filter: Optional[str] = None,
    title_query: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    q = "SELECT * FROM entities WHERE 1=1"
    args: list = []
    if exclude_workspace:
        q += " AND id != 'workspace'"
    if not include_archived:
        q += " AND status != 'archived'"
    if type_filter:
        q += " AND type = ?"
        args.append(type_filter)
    if title_query:
        q += " AND lower(title) LIKE ?"
        args.append(f"%{title_query.lower()}%")
    q += " ORDER BY pinned DESC, created_at"
    if limit is not None:
        q += " LIMIT ? OFFSET ?"
        args.append(int(limit)); args.append(int(offset))
    with _conn() as c:
        return [_row_to_entity(r) for r in c.execute(q, args).fetchall()]


def count_entities(
    *,
    include_archived: bool = True,
    type_filter: Optional[str] = None,
    title_query: Optional[str] = None,
) -> int:
    q = "SELECT COUNT(*) AS n FROM entities WHERE id != 'workspace'"
    args: list = []
    if not include_archived:
        q += " AND status != 'archived'"
    if type_filter:
        q += " AND type = ?"; args.append(type_filter)
    if title_query:
        q += " AND lower(title) LIKE ?"; args.append(f"%{title_query.lower()}%")
    with _conn() as c:
        return c.execute(q, args).fetchone()["n"]


def update_entity(entity_id: str, **fields) -> Optional[dict]:
    """
    Partial update. Accepted fields: title, notes, tags, pinned, status.
    Other keys are silently ignored. Returns the updated entity, or None
    if it doesn't exist.
    """
    allowed = {"title", "notes", "tags", "pinned", "status"}
    sets = []
    args = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags" and isinstance(v, list):
            sets.append("tags = ?"); args.append(json.dumps(v))
        elif k == "pinned":
            sets.append("pinned = ?"); args.append(1 if v else 0)
        else:
            sets.append(f"{k} = ?"); args.append(v)
    if not sets:
        return get_entity(entity_id)
    sets.append("updated_at = ?"); args.append(_utcnow())
    args.append(entity_id)
    with _conn() as c:
        cur = c.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
        if cur.rowcount == 0:
            return None
    return get_entity(entity_id)


def archive_entity(entity_id: str) -> Optional[dict]:
    """Soft-delete: mark as archived and record deleted_at."""
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "UPDATE entities SET status='archived', deleted_at=?, updated_at=? "
            "WHERE id = ? AND id != 'workspace'",
            (now, now, entity_id),
        )
        c.commit()
        if cur.rowcount == 0:
            return None
    return get_entity(entity_id)


def restore_entity(entity_id: str) -> Optional[dict]:
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "UPDATE entities SET status='active', deleted_at=NULL, updated_at=? WHERE id = ?",
            (now, entity_id),
        )
        c.commit()
        if cur.rowcount == 0:
            return None
    return get_entity(entity_id)


# ---------- Advisor notes ----------

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
        return cur.lastrowid


def log_context_assembly(
    session_id: str,
    turn_index: int,
    focus_entity_id: Optional[str],
    focus_entity_type: Optional[str],
    fields_preloaded: list[str],
    tool_calls: list[str],
    turn_text_len: int,
) -> int:
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO context_assemblies "
            "(session_id, turn_index, focus_entity_id, focus_entity_type, "
            " fields_preloaded, tool_calls, n_tool_calls, turn_text_len, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, turn_index, focus_entity_id, focus_entity_type,
                json.dumps(fields_preloaded), json.dumps(tool_calls),
                len(tool_calls), turn_text_len, now,
            ),
        )
        c.commit()
        return cur.lastrowid


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


def list_context_suggestions(status: Optional[str] = "pending") -> list[dict]:
    q = "SELECT * FROM context_suggestions"
    args: list = []
    if status:
        q += " WHERE status = ?"; args.append(status)
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


def list_advisor_notes(entity_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, entity_id, advisor, text, metadata, status, created_at "
            "FROM advisor_notes WHERE entity_id = ? AND status = 'active' "
            "ORDER BY id",
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
        }
        for r in rows
    ]


# ---------- Edges ----------

def add_edge(source_id: str, target_id: str, rel_type: str,
             metadata: Optional[dict] = None) -> None:
    """Insert an edge; idempotent via UNIQUE(source, target, rel)."""
    now = _utcnow()
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO entity_edges "
            "(source_id, target_id, rel_type, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (source_id, target_id, rel_type,
             json.dumps(metadata) if metadata else None, now),
        )
        c.commit()


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


# ---------- Messages (entity-scoped) ----------

def append_message(
    role: str,
    content_blocks: list,
    *,
    entity_id: str = WORKSPACE_ID,
    focus_entity_id: Optional[str] = None,
) -> int:
    """
    Append a message to a thread.

    Most messages live on the WORKSPACE_ID thread (the project's running
    conversation). `focus_entity_id` records which entity the user was
    looking at when this message was sent — used to highlight or filter
    later, never to switch the conversation thread.
    """
    ts = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (entity_id, focus_entity_id, role, content, ts) VALUES (?, ?, ?, ?, ?)",
            (entity_id, focus_entity_id, role, json.dumps(content_blocks), ts),
        )
        c.commit()
        return cur.lastrowid


def get_messages(entity_id: str = WORKSPACE_ID) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content, ts, focus_entity_id FROM messages "
            "WHERE entity_id = ? ORDER BY id",
            (entity_id,),
        ).fetchall()
    return [
        {
            "role": r["role"],
            "content": json.loads(r["content"]),
            "ts": r["ts"],
            "focus_entity_id": r["focus_entity_id"],
        }
        for r in rows
    ]


def clear_messages(entity_id: str = WORKSPACE_ID):
    with _conn() as c:
        c.execute("DELETE FROM messages WHERE entity_id = ?", (entity_id,))
        c.commit()


# ---------- Legacy aliases (Phase-0 callers) ----------

def get_all_messages():
    """Legacy: returns workspace-scoped messages."""
    return get_messages(WORKSPACE_ID)


def clear_history():
    """Legacy: clears workspace-scoped messages only."""
    clear_messages(WORKSPACE_ID)
