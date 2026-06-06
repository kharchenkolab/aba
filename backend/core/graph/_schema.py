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

# ABA_DB_PATH_OVERRIDE is honored too: it's the e2e-harness "override the DB
# path" signal (projects.py treats either as single-project mode). Without this
# the override only flipped SINGLE mode while DB_PATH silently stayed aba.db —
# so harnesses using it wrote to the real dev DB (test-isolation + DB-safety bug).
def _default_db_path() -> Path:
    """Default workspace DB: under ABA_RUNTIME_DIR if set/defaulted; otherwise the
    legacy backend/aba.db (pre-runtime-split). Resolved lazily so an ABA_RUNTIME_DIR
    env-var override at startup is honored without importing config here."""
    rd = os.environ.get("ABA_RUNTIME_DIR")
    if rd: return Path(rd) / "aba.db"
    # Match core.config's default so the two stay in lockstep.
    return Path("/workspace/aba-runtime") / "aba.db"


DB_PATH = Path(
    os.environ.get("ABA_DB_PATH")
    or os.environ.get("ABA_DB_PATH_OVERRIDE")
    or _default_db_path()
)

# Root entity that hosts any chat not yet scoped to a specific entity.
WORKSPACE_ID = "workspace"

# Global default-disabled tools (comma-separated names). Operator kill-switch
# read once at startup; layered under each agent's tool_allowlist.
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


def _project_conn(project_id: Optional[str] = None):
    """Open a sqlite connection to a SPECIFIC project's DB without mutating
    the global DB_PATH. Used by the job runner — workers are global but
    jobs live in per-project DBs, so the worker has to address a specific
    project's DB without disturbing whatever project the HTTP path is
    currently serving.

    `project_id=None` falls back to the current DB_PATH — preserves the
    legacy callsite behaviour."""
    if project_id is None:
        return _conn()
    from core.config import project_db_path
    p = project_db_path(project_id)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _column_exists(c: sqlite3.Connection, table: str, col: str) -> bool:
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


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
            ("tags",         "ALTER TABLE entities ADD COLUMN tags TEXT"),
            ("notes",        "ALTER TABLE entities ADD COLUMN notes TEXT"),
            ("pinned",       "ALTER TABLE entities ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0"),
            ("deleted_at",   "ALTER TABLE entities ADD COLUMN deleted_at TEXT"),
            # F3 (files.md): derived display path from title + conventions.
            # Idempotent; recomputed by bio.graph.display.recompute_display_path.
            ("display_path", "ALTER TABLE entities ADD COLUMN display_path TEXT"),
            # Stage 2 of misc/exec_records_and_versioning.md: artifact pointer
            # into the exec record that produced this entity. Together with
            # artifact_kind + artifact_idx these form <exec_id>:<kind>:<idx>,
            # the canonical artifact id. For now, new figure/table entities
            # populate these alongside producing_code (denormalized cache for
            # legacy read paths); the column drop comes later once reads
            # migrate to the exec_records-backed helper.
            ("exec_id",       "ALTER TABLE entities ADD COLUMN exec_id TEXT"),
            ("artifact_kind", "ALTER TABLE entities ADD COLUMN artifact_kind TEXT"),
            ("artifact_idx",  "ALTER TABLE entities ADD COLUMN artifact_idx INTEGER"),
        ):
            if not _column_exists(c, "entities", col):
                c.execute(ddl)

        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_parent ON entities(parent_entity_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_status ON entities(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_pinned ON entities(pinned)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_entities_exec ON entities(exec_id)")
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

        # Phase C.5 migration: old `advisor_notes` table → neutral
        # `agent_notes`. Idempotent — only renames when the old name
        # exists and the new doesn't. The CREATE below then no-ops on
        # the renamed table.
        if _table_exists(c, "advisor_notes") and not _table_exists(c, "agent_notes"):
            c.execute("ALTER TABLE advisor_notes RENAME TO agent_notes")
        if _table_exists(c, "agent_notes") and _column_exists(c, "agent_notes", "advisor") \
                and not _column_exists(c, "agent_notes", "role"):
            c.execute("ALTER TABLE agent_notes RENAME COLUMN advisor TO role")
        c.execute("""
            CREATE TABLE IF NOT EXISTS agent_notes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id    TEXT NOT NULL,
                role         TEXT NOT NULL,
                text         TEXT NOT NULL,
                metadata     TEXT,
                status       TEXT NOT NULL DEFAULT 'active',
                created_at   TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_notes_entity ON agent_notes(entity_id)")

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
        if not _column_exists(c, "context_assemblies", "manifest_json"):
            # T2.4: full Manifest snapshot for the drawer UI.
            c.execute("ALTER TABLE context_assemblies ADD COLUMN manifest_json TEXT")

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

        # Phase C.5 migration: proposals.advisor → proposals.role.
        if _table_exists(c, "proposals") and _column_exists(c, "proposals", "advisor") \
                and not _column_exists(c, "proposals", "role"):
            c.execute("ALTER TABLE proposals RENAME COLUMN advisor TO role")
        c.execute("""
            CREATE TABLE IF NOT EXISTS proposals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id   TEXT,
                kind        TEXT NOT NULL,
                role        TEXT NOT NULL DEFAULT 'primary',
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

        # Runs — Turn checkpoints (arch3_plan.md Pass E). One row per agent
        # turn (Guide or advisor); state transitions upsert via run_id. Lets
        # resume-after-restart happen without _repair_tool_pairs heuristics.
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id          TEXT PRIMARY KEY,
                session_id      TEXT,
                turn_index      INTEGER,
                agent_spec_name TEXT,
                state           TEXT,
                focus_entity_id TEXT,
                thread_id       TEXT,
                pending_blob    TEXT,        -- JSON: pending_tool_calls + final_message
                error_blob      TEXT,        -- JSON: ErrorDetail
                usage_blob      TEXT,        -- JSON: usage counters
                started_at      TEXT,
                updated_at      TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_state ON runs(state)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_updated ON runs(updated_at)")

        # Exec records (misc/exec_records_and_versioning.md). One row per
        # tool dispatch that produces an artifact or material side effect
        # (currently run_python / run_r). Index only — the full record lives
        # at record_path as a JSON sidecar colocated in the workdir's .exec/
        # subdir. code_hash supports dedup / "same code as before" lookups
        # without rehydrating the JSON. Separate from tool_invocations
        # (which is bare telemetry — duration, status, no code, no produced).
        c.execute("""
            CREATE TABLE IF NOT EXISTS execution_records (
                exec_id        TEXT PRIMARY KEY,
                thread_id      TEXT NOT NULL,
                run_id         TEXT,
                tool_use_id    TEXT,
                tool_name      TEXT NOT NULL,
                status         TEXT NOT NULL,
                code_hash      TEXT,
                record_path    TEXT NOT NULL,
                started_at     TEXT NOT NULL,
                completed_at   TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_exec_thread ON execution_records(thread_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_exec_run    ON execution_records(run_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_exec_hash   ON execution_records(code_hash)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_exec_tooluse ON execution_records(tool_use_id)")

        # P3 #6 — per-tool-invocation telemetry. One row per execute_tool
        # dispatch. Aggregated by /api/admin/tool_stats so we can see
        # what's actually used + failure rates as the catalog grows.
        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_invocations (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        TEXT,
                agent_spec    TEXT,        -- 'guide' | advisor name
                tool_name     TEXT,        -- e.g. 'run_python' or 'mcp_server:tool'
                source        TEXT,        -- 'bio' | 'mcp:<server>' | 'core'
                status        TEXT,        -- 'ok' | 'error' | 'rejected' | 'deferred'
                input_summary TEXT,        -- truncated input for audit
                duration_ms   INTEGER,
                error_summary TEXT,
                started_at    TEXT,
                ended_at      TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_tool_inv_run ON tool_invocations(run_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tool_inv_name ON tool_invocations(tool_name)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tool_inv_when ON tool_invocations(started_at)")

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

    # Stage 2 cutover (misc/exec_records_and_versioning.md): backfill
    # synthetic exec records for any entity that has producing_code but
    # no exec_id. Idempotent + cheap when there's nothing to backfill.
    # Wrapped in try/except so a bad row doesn't break init_db; the
    # function itself swallows per-row errors and aggregates a count.
    try:
        from core.graph.exec_records import backfill_legacy_producing_code
        backfill_legacy_producing_code()
    except Exception:  # noqa: BLE001 — init_db must complete; backfill is best-effort
        import logging as _lg
        _lg.getLogger(__name__).warning(
            "init_db: backfill_legacy_producing_code failed (continuing)",
            exc_info=True,
        )
