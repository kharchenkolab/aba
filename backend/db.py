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
        # v3: messages belong to a thread (line of inquiry). NULL = the default
        # thread (small projects never create a named one).
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

        # Jobs — background tool executions (long pipelines). The chat turn
        # returns immediately; the worker runs the job and registers
        # artifacts when it finishes.
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

        # Events — append-only activity/audit log. Feeds the Home activity
        # feed (Phase 13) and the audit view (Phase 24).
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

        # Per-tool enabled state (Phase 14). Absent row = enabled.
        c.execute("""
            CREATE TABLE IF NOT EXISTS tool_settings (
                name     TEXT PRIMARY KEY,
                enabled  INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Proactive proposals (Phase D). The Guide/advisors notice something and
        # propose an action (draft a claim, set a question, file an OQ). Every
        # proposal is attributed, dismissible, reversible, and de-duplicated by
        # `signature` across ALL statuses — a dismissed proposal does not re-nag
        # until the underlying world changes (a new signature).
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
    # Log meaningful entity creations to the activity/audit feed.
    if entity_type not in ("workspace", "analysis"):
        kind = "scenario_created" if scenario_of else "entity_created"
        log_event(kind, entity_id=eid, title=title, detail={"type": entity_type})
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
    allowed = {"title", "notes", "tags", "pinned", "status", "metadata", "artifact_path"}
    sets = []
    args = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags" and isinstance(v, list):
            sets.append("tags = ?"); args.append(json.dumps(v))
        elif k == "metadata":
            sets.append("metadata = ?"); args.append(json.dumps(v) if v is not None else None)
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


# ---------- Result (kept observation) member management ----------
# A `result` entity is a kept grouping: metadata.members is an ordered list of
# panels, each {id, kind: figure|table|value|text, ref?, text?, caption?}. The
# single-cell case (one member) is the common one; results grow deliberately.

def _result_members(e: dict) -> list:
    return list(((e.get("metadata") or {}).get("members")) or [])


def _save_members(result_id: str, members: list) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    meta = dict(e.get("metadata") or {})
    meta["members"] = members
    return update_entity(result_id, metadata=meta)


def add_result_member(result_id: str, *, kind: str, ref: Optional[str] = None,
                      text: Optional[str] = None, caption: str = "",
                      at: Optional[int] = None) -> Optional[dict]:
    """Append (or insert at `at`) a panel. Figures/tables/values carry a `ref`
    to the cell entity; text panels carry inline `text`."""
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    members = _result_members(e)
    member = {"id": gen_entity_id("m"), "kind": kind, "caption": caption}
    if ref:
        member["ref"] = ref
    if text is not None:
        member["text"] = text
    if at is None or at < 0 or at > len(members):
        members.append(member)
    else:
        members.insert(at, member)
    return _save_members(result_id, members)


def remove_result_member(result_id: str, member_id: str) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    members = [m for m in _result_members(e) if m.get("id") != member_id]
    return _save_members(result_id, members)


def update_result_member(result_id: str, member_id: str, **fields) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    members = _result_members(e)
    for m in members:
        if m.get("id") == member_id:
            for k in ("caption", "text"):
                if k in fields and fields[k] is not None:
                    m[k] = fields[k]
    return _save_members(result_id, members)


def reorder_result_members(result_id: str, ordered_ids: list) -> Optional[dict]:
    e = get_entity(result_id)
    if not e or e["type"] != "result":
        return None
    by_id = {m.get("id"): m for m in _result_members(e)}
    members = [by_id[i] for i in ordered_ids if i in by_id]
    # keep any not mentioned (defensive) at the end, original order
    members += [m for m in _result_members(e) if m.get("id") not in set(ordered_ids)]
    return _save_members(result_id, members)


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
        note_id = cur.lastrowid
    ent = get_entity(entity_id)
    log_event("advisor_note", entity_id=entity_id,
              title=(ent["title"] if ent else None), detail={"advisor": advisor})
    return note_id


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


# Global default-disabled tools (comma-separated names), e.g. to keep the Guide's
# context minimal while skills/tooling aren't being tested. Per-project settings
# layer on top. Set ABA_DISABLED_TOOLS="inspect_upload,create_scenario,..." .
_GLOBAL_DISABLED = {t.strip() for t in os.environ.get("ABA_DISABLED_TOOLS", "").split(",") if t.strip()}


def get_disabled_tools() -> set[str]:
    with _conn() as c:
        try:
            rows = c.execute("SELECT name FROM tool_settings WHERE enabled=0").fetchall()
        except sqlite3.OperationalError:
            return set(_GLOBAL_DISABLED)
    return {r["name"] for r in rows} | _GLOBAL_DISABLED


def set_tool_enabled(name: str, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO tool_settings (name, enabled) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled",
            (name, 1 if enabled else 0),
        )
        c.commit()


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


def _row_to_job(r) -> dict:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "status": r["status"],
        "focus_entity_id": r["focus_entity_id"],
        "params": json.loads(r["params"]) if r["params"] else None,
        "log_tail": r["log_tail"],
        "error": r["error"],
        "created_at": r["created_at"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
    }


def create_job(job_id: str, kind: str, title: str, focus_entity_id: Optional[str],
               params: dict) -> dict:
    now = _utcnow()
    with _conn() as c:
        c.execute(
            "INSERT INTO jobs (id, kind, title, status, focus_entity_id, params, created_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, kind, title, focus_entity_id, json.dumps(params), now),
        )
        c.commit()
    return get_job(job_id)  # type: ignore[return-value]


def get_job(job_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(r) if r else None


def list_jobs(limit: int = 50) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def update_job(job_id: str, **fields) -> None:
    allowed = {"status", "log_tail", "error", "started_at", "finished_at"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?"); args.append(v)
    if not sets:
        return
    args.append(job_id)
    with _conn() as c:
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()


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
            # The subject the note is about, so the note is self-describing
            # even when the conversation has scrolled past.
            "entity_type": r["e_type"],
            "entity_title": r["e_title"],
        }
        for r in rows
    ]


# ---------- Proposals (Phase D) ----------

def _row_to_proposal(r) -> dict:
    return {
        "id": r["id"],
        "thread_id": r["thread_id"],
        "kind": r["kind"],
        "advisor": r["advisor"],
        "headline": r["headline"],
        "body": r["body"],
        "payload": json.loads(r["payload"]) if r["payload"] else None,
        "signature": r["signature"],
        "status": r["status"],
        "result_id": r["result_id"],
        "undo_data": json.loads(r["undo_data"]) if r["undo_data"] else None,
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def proposal_signature_exists(signature: str) -> bool:
    """Dedup gate: has this exact proposal ever been raised (any status)? We
    suppress re-raising across pending/accepted/dismissed so a dismissed idea
    doesn't re-nag until the world changes (which yields a new signature)."""
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM proposals WHERE signature = ? LIMIT 1", (signature,)
        ).fetchone()
    return row is not None


def add_proposal(*, thread_id: Optional[str], kind: str, headline: str,
                 signature: str, advisor: str = "guide", body: str = "",
                 payload: Optional[dict] = None) -> Optional[int]:
    """Insert a pending proposal. Returns None (no-op) if an identical-signature
    proposal already exists."""
    if proposal_signature_exists(signature):
        return None
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO proposals (thread_id, kind, advisor, headline, body, "
            "payload, signature, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (thread_id, kind, advisor, headline, body,
             json.dumps(payload) if payload else None, signature, now, now),
        )
        c.commit()
        pid = cur.lastrowid
    log_event("proposal", entity_id=thread_id, title=headline,
              detail={"kind": kind, "advisor": advisor})
    return pid


def list_proposals(thread_id: Optional[str] = None,
                   status: Optional[str] = "pending") -> list[dict]:
    q = "SELECT * FROM proposals"
    conds, args = [], []
    if thread_id is not None:
        conds.append("thread_id = ?"); args.append(thread_id)
    if status is not None:
        conds.append("status = ?"); args.append(status)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_proposal(r) for r in rows]


def get_proposal(pid: int) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    return _row_to_proposal(row) if row else None


def update_proposal(pid: int, *, status: Optional[str] = None,
                    result_id: Optional[str] = None,
                    undo_data: Optional[dict] = None) -> bool:
    sets, args = [], []
    if status is not None:
        sets.append("status = ?"); args.append(status)
    if result_id is not None:
        sets.append("result_id = ?"); args.append(result_id)
    if undo_data is not None:
        sets.append("undo_data = ?"); args.append(json.dumps(undo_data))
    if not sets:
        return False
    sets.append("updated_at = ?"); args.append(_utcnow())
    args.append(pid)
    with _conn() as c:
        cur = c.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
        return cur.rowcount > 0


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


def find_active_figure_by_title(title: str) -> Optional[dict]:
    """Most-recent active figure with this exact title (for version chains)."""
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM entities WHERE type='figure' AND title=? "
            "AND status='active' ORDER BY created_at DESC LIMIT 1",
            (title,),
        ).fetchone()
        return _row_to_entity(r) if r else None


def figure_history(entity_id: str) -> list[dict]:
    """
    Return the version chain for a figure, newest first. Follows
    wasRevisionOf edges in both directions from the given entity.
    """
    # Walk back (older) and forward (newer) along wasRevisionOf.
    chain_ids: list[str] = [entity_id]
    # Older: this --wasRevisionOf--> older
    cur = entity_id
    while True:
        with _conn() as c:
            r = c.execute(
                "SELECT target_id FROM entity_edges WHERE source_id=? AND rel_type='wasRevisionOf'",
                (cur,),
            ).fetchone()
        if not r:
            break
        cur = r["target_id"]
        if cur in chain_ids:
            break
        chain_ids.append(cur)
    # Newer: newer --wasRevisionOf--> this
    cur = entity_id
    while True:
        with _conn() as c:
            r = c.execute(
                "SELECT source_id FROM entity_edges WHERE target_id=? AND rel_type='wasRevisionOf'",
                (cur,),
            ).fetchone()
        if not r:
            break
        cur = r["source_id"]
        if cur in chain_ids:
            break
        chain_ids.insert(0, cur)
    return [e for e in (get_entity(i) for i in chain_ids) if e]


def remove_edge(source_id: str, target_id: str, rel_type: str) -> None:
    with _conn() as c:
        c.execute(
            "DELETE FROM entity_edges WHERE source_id = ? AND target_id = ? AND rel_type = ?",
            (source_id, target_id, rel_type),
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
    thread_id: Optional[str] = None,
) -> int:
    """
    Append a message to the project conversation.

    `focus_entity_id` records which entity the user was looking at when this
    message was sent. `thread_id` is the line of inquiry it belongs to (v3);
    NULL means the default thread.
    """
    ts = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO messages (entity_id, focus_entity_id, thread_id, role, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entity_id, focus_entity_id, thread_id, role, json.dumps(content_blocks), ts),
        )
        c.commit()
        return cur.lastrowid


def create_thread(title: str, question: str = "") -> str:
    """A thread is an entity (type='thread') carrying the line-of-inquiry fields
    in metadata: question, open_questions[], lifecycle (open|parked|concluded)."""
    return create_entity(
        entity_type="thread", title=title or "Untitled investigation",
        metadata={"question": question, "open_questions": [], "lifecycle": "open"},
    )


def list_threads() -> list[dict]:
    return list_entities(type_filter="thread", include_archived=False)


def find_default_thread() -> Optional[str]:
    """The project's default thread entity (metadata.is_default), or None if it
    hasn't been materialized yet."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, metadata FROM entities WHERE type='thread' "
            "AND deleted_at IS NULL AND status != 'archived'"
        ).fetchall()
    for r in rows:
        m = json.loads(r["metadata"]) if r["metadata"] else {}
        if m.get("is_default"):
            return r["id"]
    return None


def get_or_create_default_thread() -> str:
    """Lazily materialize the default thread into a real entity and adopt any
    previously unthreaded messages. Lets 'Main' graduate (gain a header) instead
    of being a permanent headerless bucket."""
    tid = find_default_thread()
    if tid:
        return tid
    tid = create_entity(
        entity_type="thread", title="Main thread",
        metadata={"question": "", "open_questions": [], "lifecycle": "open", "is_default": True},
    )
    with _conn() as c:
        c.execute("UPDATE messages SET thread_id = ? WHERE thread_id IS NULL", (tid,))
        c.commit()
    return tid


def find_kept_note(source_key: str) -> Optional[str]:
    """Return the id of an active kept-note snapshot for this message key."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id, metadata FROM entities WHERE type='note' "
            "AND status='active' AND deleted_at IS NULL"
        ).fetchall()
    for r in rows:
        m = json.loads(r["metadata"]) if r["metadata"] else {}
        if m.get("source_key") == source_key:
            return r["id"]
    return None


def search(q: str, limit: int = 25) -> dict:
    """Faceted search across the firehose: entities (title/notes) + message
    text snippets. Lexical (LIKE) for now; FTS/semantic can replace later."""
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
            continue  # match was in a non-text block (tool args etc.) — skip
        start = max(0, idx - 40)
        snippet = ("…" if start else "") + text[start:idx + len(q) + 60].strip() + "…"
        messages.append({"id": r["id"], "role": r["role"], "ts": r["ts"], "snippet": snippet})
    return {"entities": entities, "messages": messages}


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


# ---------- Legacy aliases (Phase-0 callers) ----------

def get_all_messages():
    """Legacy: returns workspace-scoped messages."""
    return get_messages(WORKSPACE_ID)


def clear_history():
    """Legacy: clears workspace-scoped messages only."""
    clear_messages(WORKSPACE_ID)
