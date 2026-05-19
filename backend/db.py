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
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_entity_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_messages_entity ON messages(entity_id)")

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
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def get_entity(entity_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return _row_to_entity(r) if r else None


def list_entities(*, exclude_workspace: bool = False) -> list[dict]:
    q = "SELECT * FROM entities"
    if exclude_workspace:
        q += " WHERE id != 'workspace'"
    q += " ORDER BY created_at"
    with _conn() as c:
        return [_row_to_entity(r) for r in c.execute(q).fetchall()]


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
