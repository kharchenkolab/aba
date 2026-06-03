"""Multi-project registry.

Each project is its own SQLite DB under ``backend/projects/<id>.db``. Artifacts
and uploaded data stay global (content-addressed), so only the DB path is
per-project. The active project's path is published to ``db.DB_PATH`` and every
``db.*`` function operates on it.

Bypassed entirely in single-project / test mode — when ``ABA_DB_PATH`` or
``ABA_DB_PATH_OVERRIDE`` is set, the e2e harness owns ``db.DB_PATH`` and this
layer just runs ``init_db`` on it.
"""
from __future__ import annotations
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.graph import _schema as _schema_mod
from core.graph._schema import init_db
from core.graph.entities import update_entity

# Per-project state is consolidated under PROJECTS_DIR/<pid>/ (data, work,
# artifacts, project.db) — see core.config.project_root() and friends.
# ABA_PROJECTS_DIR overrides the location for tests/eval-audit isolation.
from core.config import PROJECTS_DIR  # noqa: E402 — kept here so legacy `from core.projects import PROJECTS_DIR` keeps working
REGISTRY = PROJECTS_DIR / "registry.json"
SCRATCH = PROJECTS_DIR / "_scratch.db"   # parked here when no project is active
SINGLE = bool(os.environ.get("ABA_DB_PATH") or os.environ.get("ABA_DB_PATH_OVERRIDE"))

_state = {"current": None}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list:
    if REGISTRY.exists():
        try:
            return json.loads(REGISTRY.read_text())
        except Exception:
            return []
    return []


def _save(reg: list) -> None:
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def _db_file(pid: str) -> Path:
    """Per-project DB file: projects/<pid>/project.db (post 2026-05-31 reorg).
    Auto-creates the parent project dir so create_project doesn't need to."""
    from core.config import project_db_path
    return project_db_path(pid)


def _counts(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT type, COUNT(*) n FROM entities WHERE deleted_at IS NULL "
            "AND status != 'archived' AND type != 'workspace' GROUP BY type"
        ).fetchall()
        c.close()
        return {r["type"]: r["n"] for r in rows}
    except Exception:
        return {}


def _park_scratch() -> None:
    """No active project: point the DB connection at a throwaway DB so db.*
    calls don't crash. The scratch DB is never registered, so it never
    shows on Home."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    _schema_mod.set_db_path(SCRATCH)
    _state["current"] = None
    init_db()


def init() -> None:
    """Startup: in test mode just init the harness DB; otherwise PARK on scratch.

    We deliberately do NOT auto-pick a project here. Earlier behavior set
    current = reg[-1] (last in registry), which silently routed any pid-less
    request to the wrong project after a server bounce — including chat turns
    when the frontend lost context. The page URL knows which project it's on;
    each request now carries project_id, and the handler set_current()s before
    doing DB work. If a request arrives without project_id and the global is
    None, the handler can refuse loudly instead of writing to a misleading
    default."""
    if SINGLE:
        init_db()
        return
    _park_scratch()


def set_current(pid: str) -> None:
    if SINGLE:
        return
    _schema_mod.set_db_path(_db_file(pid))
    _state["current"] = pid
    init_db()          # idempotent — ensures tables exist
    # Note: deliberately NOT calling _touch(pid) here. Project selection (= the
    # user clicking a project on the Home screen) is a navigation event, not a
    # work event — PK 2026-06-02 wanted the right-column ordering driven by
    # actual project activity (chat, entities, runs), not by mere "I clicked
    # this to look at it." last_touched is now derived from the DB file's
    # mtime in list_projects(), so it reflects real work automatically.
    # F3: backfill display_path for any entity in this project that
    # predates the column / bio's layout computers. Cheap; idempotent.
    try:
        from content.bio.graph.display import backfill_missing_display_paths
        backfill_missing_display_paths()
    except Exception:  # noqa: BLE001
        pass           # never block project switch on a backfill failure
    # A1: reap stale Turn rows + repair any orphaned tool_use in the
    # newly-opened project's message log. Idempotent; safe to run on
    # every project switch.
    try:
        from core.runtime.checkpoint import reap_stale_turns
        reap_stale_turns()
    except Exception:  # noqa: BLE001
        pass


def current() -> str | None:
    if SINGLE:
        return "single"
    return _state["current"]


def _touch(pid: str) -> None:
    reg = _load()
    for p in reg:
        if p["id"] == pid:
            p["last_touched"] = _now()
    _save(reg)


def _db_mtime_iso(pid: str) -> str | None:
    """Project DB mtime as ISO-8601. None if the DB doesn't exist yet
    (briefly the case right after create_project, before any activity)."""
    try:
        from datetime import datetime, timezone
        p = _db_file(pid)
        if not p.exists():
            return None
        return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
    except Exception:  # noqa: BLE001
        return None


def list_projects() -> list:
    if SINGLE:
        return [{"id": "single", "name": "Project", "created_at": _now(),
                 "last_touched": _now(), "current": True, "counts": _counts(_schema_mod.DB_PATH)}]
    cur = _state["current"]
    out = []
    for p in _load():
        # last_touched ← DB file mtime if available, else the registry value
        # (which is set on creation). The DB mtime captures real activity
        # automatically — selecting a project doesn't write to the DB, so it
        # doesn't bump the time.
        last = _db_mtime_iso(p["id"]) or p.get("last_touched") or p.get("created_at")
        out.append({**p, "last_touched": last,
                    "current": p["id"] == cur,
                    "counts": _counts(_db_file(p["id"]))})
    return out


def create_project(name: str) -> dict:
    if SINGLE:
        return list_projects()[0]
    reg = _load()
    pid = "prj_" + uuid.uuid4().hex[:8]
    entry = {"id": pid, "name": (name or "Untitled project").strip()[:80],
             "created_at": _now(), "last_touched": _now()}
    reg.append(entry)
    _save(reg)
    set_current(pid)
    update_entity("workspace", title=entry["name"])  # in-project title = project name
    return {**entry, "current": True, "counts": {}}


def rename_project(pid: str, name: str) -> None:
    if SINGLE:
        return
    reg = _load()
    for p in reg:
        if p["id"] == pid:
            p["name"] = (name or p["name"]).strip()[:80]
    _save(reg)


def delete_project(pid: str) -> None:
    if SINGLE:
        return
    reg = [p for p in _load() if p["id"] != pid]
    _save(reg)
    f = _db_file(pid)
    if f.exists():
        f.unlink()
    if _state["current"] == pid:
        if reg:
            set_current(reg[-1]["id"])
        else:
            _park_scratch()       # true empty state — no phantom project
