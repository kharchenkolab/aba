"""DB schema + connection primitives. The single owner of init_db and _conn.

Per arch3_plan.md Pass B: this is the foundation that all other
core/graph/* modules import. Bio entity-type names appear here only in
the bootstrap insert (a single row creating the workspace entity); the
TYPES seam check tolerates this.
"""
import sqlite3
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("ABA_DB_PATH") or (Path(__file__).parent.parent.parent / "aba.db"))

# Root entity that hosts any chat not yet scoped to a specific entity.
WORKSPACE_ID = "workspace"

# Global default-disabled tools (comma-separated names). Per-project
# settings layer on top via the tool_settings table.
_GLOBAL_DISABLED = {t.strip() for t in os.environ.get("ABA_DISABLED_TOOLS", "").split(",") if t.strip()}


def set_db_path(path) -> None:
    """Repoint the connection at a new SQLite DB. The multi-project layer
    (core.projects) uses this to swap the active project's database
    in-place. _conn() does a runtime lookup of the module-global DB_PATH
    so the next call sees the new path."""
    global DB_PATH
    DB_PATH = Path(path)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _column_exists(c: sqlite3.Connection, table: str, col: str) -> bool:
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def gen_entity_id(prefix: str = "ent") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


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
        if not _column_exists(c, "messages", "thread_id"):
            c.execute("ALTER TABLE messages ADD COLUMN thread_id TEXT")

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

        c.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                kind            TEXT NOT NULL,
                title           TEXT,
                status          TEXT NOT NULL DEFAULT 'queued',
                focus_entity_id TEXT,
                params          TEXT,
                log_tail        TEXT,
                error           TEXT,
                created_at      TEXT NOT NULL,
                started_at      TEXT,
                finished_at     TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                kind        TEXT NOT NULL,
                entity_id   TEXT,
                title       TEXT,
                detail      TEXT,
                ts          TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_settings (
                name     TEXT PRIMARY KEY,
                enabled  INTEGER NOT NULL DEFAULT 1
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id   TEXT,
                kind        TEXT NOT NULL,
                advisor     TEXT NOT NULL DEFAULT 'guide',
                headline    TEXT NOT NULL,
                body        TEXT,
                payload     TEXT,
                signature   TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                result_id   TEXT,
                undo_data   TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_prop_thread ON proposals(thread_id, status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_prop_sig ON proposals(signature)")

        # Bootstrap workspace entity (opaque type 'workspace' — the seam-check
        # tolerates this because workspace is not a bio entity kind).
        row = c.execute("SELECT id FROM entities WHERE id = ?", (WORKSPACE_ID,)).fetchone()
        if not row:
            now = _utcnow()
            c.execute(
                "INSERT INTO entities (id, type, title, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (WORKSPACE_ID, "workspace", "Workspace", "active", now, now),
            )
        c.commit()
