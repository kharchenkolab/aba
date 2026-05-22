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

import db

BASE = Path(__file__).parent
# Override with ABA_PROJECTS_DIR to isolate the registry (tests, eval audits).
PROJECTS_DIR = Path(os.environ.get("ABA_PROJECTS_DIR") or (BASE / "projects"))
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
    return PROJECTS_DIR / f"{pid}.db"


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
    """No active project: point db.DB_PATH at a throwaway DB so db.* calls don't
    crash. The scratch DB is never registered, so it never shows on Home."""
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    db.DB_PATH = SCRATCH
    _state["current"] = None
    db.init_db()


def init() -> None:
    """Startup: in test mode just init the harness DB; otherwise resume the most
    recent project, or park on scratch when there are none (true empty state)."""
    if SINGLE:
        db.init_db()
        return
    reg = _load()
    if not reg:
        _park_scratch()
        return
    set_current(reg[-1]["id"])


def set_current(pid: str) -> None:
    if SINGLE:
        return
    db.DB_PATH = _db_file(pid)
    _state["current"] = pid
    db.init_db()          # idempotent — ensures tables exist
    _touch(pid)


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


def list_projects() -> list:
    if SINGLE:
        return [{"id": "single", "name": "Project", "created_at": _now(),
                 "last_touched": _now(), "current": True, "counts": _counts(db.DB_PATH)}]
    cur = _state["current"]
    return [{**p, "current": p["id"] == cur, "counts": _counts(_db_file(p["id"]))}
            for p in _load()]


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
    db.update_entity("workspace", title=entry["name"])   # in-project title = project name
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
