"""Multi-project registry.

Each project is its own SQLite DB under ``backend/projects/<id>.db``. Artifacts
and uploaded data stay global (content-addressed), so only the DB path is
per-project. The active project's path is published to ``db.DB_PATH`` and every
``db.*`` function operates on it.

Bypassed entirely in single-project / test mode — when ``ABA_DB_PATH`` is set,
the e2e harness owns ``db.DB_PATH`` and this layer just runs ``init_db`` on it.
(The former ``ABA_DB_PATH_OVERRIDE`` alias was merged into ``ABA_DB_PATH``.)
"""
from __future__ import annotations
import fcntl
import json
import os
import sqlite3
import uuid
import contextlib
import contextvars
from datetime import datetime, timezone
from pathlib import Path

from core.graph import _schema as _schema_mod
from core.graph._schema import init_db
from core.graph.entities import update_entity

# Per-project state is consolidated under PROJECTS_DIR/<pid>/ (data, work,
# artifacts, project.db) — see core.config.project_root() and friends.
# ABA_PROJECTS_DIR overrides the location for tests/eval-audit isolation.
from core import config  # noqa: E402
from core.config import PROJECTS_DIR, _LazyDir  # noqa: E402 — kept here so legacy `from core.projects import PROJECTS_DIR` keeps working
# Lazy so an ABA_PROJECTS_DIR / ABA_RUNTIME_DIR override set after import (tests,
# runtime swaps) is honored — these resolve PROJECTS_DIR live on every use.
REGISTRY = _LazyDir(lambda: PROJECTS_DIR / "registry.json")
SCRATCH = _LazyDir(lambda: PROJECTS_DIR / "_scratch.db")   # parked here when no project is active
SINGLE = bool(config.settings.db_path.get())

_state = {"current": None}

# Per-CONTEXT active project, isolated from the process-global _state above.
# Set by bind() for background turn tasks (which asyncio copies the context
# into) so a concurrent request's set_current() can't change what project a
# running turn sees. current() prefers this when set. See bind() and the
# 2026-06 cross-project corruption incident write-up.
_active_pid: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "aba_active_pid", default=None
)

# Projects already reaped in THIS process (first-open memo — see set_current/#14).
_reaped_pids: set[str] = set()
# Projects whose DB has been initialized+reaped in THIS process (ensure_opened).
_opened_pids: set[str] = set()


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


# Lock a sidecar file, NOT registry.json itself: write_text() replaces the
# inode so any flock held on registry.json would be invalidated mid-cycle.
# Holding the lock on a stable sidecar serializes the whole load→edit→save.
_REGISTRY_LOCK = "registry.lock"


@contextlib.contextmanager
def _locked_registry():
    """Serialize read-modify-write on the project registry.

    Yields the current registry as a mutable list. Mutate IN PLACE
    (`reg.append(...)`, `reg[:] = [...]`, item field edits — not
    `reg = ...`); on exit the modified list is written back. Holds an
    exclusive flock on a sidecar file the whole time, so concurrent
    callers (curl bulk-delete, the agent tool API, simultaneous UI
    clicks) queue up instead of clobbering each other.

    Background: 2026-06-10 ran 256 parallel DELETE /api/projects via
    `xargs -P 4`. Each handler did `reg = _load(); reg.remove(pid);
    _save(reg)` with no lock. End state: registry empty (every save
    overwrote a stale snapshot with one pid removed; the LAST snapshot
    was a tiny one). Fix is this context manager — every mutating
    helper goes through it. See feedback_no_parallel_destructive_api.md.
    """
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = PROJECTS_DIR / _REGISTRY_LOCK
    # O_CREAT for first-run; r+/w both work, "a" never truncates the lockfile
    # in case anyone wrote to it. Mode is irrelevant — we only use the fd.
    with open(lock_path, "a") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            reg = _load()
            yield reg
            _save(reg)
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _db_file(pid: str) -> Path:
    """Per-project DB file: projects/<pid>/project.db (post 2026-05-31 reorg).
    Auto-creates the parent project dir so create_project doesn't need to."""
    from core.config import project_db_path
    return project_db_path(pid)


# Cache project entity counts by (db path, mtime). list_projects() needs counts
# for EVERY project (the project page), which otherwise opens N SQLite DBs per
# call — the dominant cost at 200+ projects. A project's DB mtime only bumps on
# real activity, so an idle project's counts are reused without opening its DB.
_counts_cache: dict[str, tuple[float, dict]] = {}


def _counts(path) -> dict:
    p = Path(path)
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {}
    key = str(p)
    cached = _counts_cache.get(key)
    if cached is not None and cached[0] == mt:
        return cached[1]
    try:
        c = sqlite3.connect(p)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT type, COUNT(*) n FROM entities WHERE deleted_at IS NULL "
            "AND status != 'archived' AND type != 'workspace' GROUP BY type"
        ).fetchall()
        c.close()
        counts = {r["type"]: r["n"] for r in rows}
    except Exception:
        counts = {}
    _counts_cache[key] = (mt, counts)
    return counts


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
    # A1/#14: reap stale Turn rows + repair orphaned tool_use ONCE per project
    # per process — the FIRST time this process addresses the project's DB.
    # Staleness means "the owning process is gone": true only for turns left by
    # a *previous* process, which are fully present the moment we first open the
    # DB. Re-reaping on every later switch (the middleware toggles projects on
    # nearly every request) is redundant AND races live turns — a switch back to
    # a project mid-turn would fail its own running turn and synthesize a bogus
    # 'interrupted' result. Memoizing to first-open removes both problems; the
    # liveness guard inside reap_stale_turns is the belt to this suspenders.
    if pid not in _reaped_pids:
        _reaped_pids.add(pid)
        # First-open lifecycle event. The turn reaper (Reasoning plane,
        # core/runtime/checkpoint.py) registers for this — the waist fires
        # the event, it never imports upward (plane lint, W0.2).
        try:
            from core.hooks.dispatcher import dispatch
            dispatch("on_project_first_open", {"pid": pid})
        except Exception:  # noqa: BLE001
            pass
    # Content-side project-open hooks (display-path backfill, etc.) run
    # via the hook dispatcher. Errors are swallowed by dispatch() — one
    # bad hook must not block a project switch.
    try:
        from core.hooks.dispatcher import dispatch
        dispatch("on_project_open", {"pid": pid})
    except Exception:  # noqa: BLE001
        pass


def current() -> str | None:
    if SINGLE:
        return "single"
    pid = _active_pid.get()
    if pid is not None:
        return pid
    return _state["current"]


def current_project_id() -> str:
    """The active project's id, or '_workspace' as the workspace-level fallback.
    Used by code paths that need a project context but the caller didn't supply
    one (e.g. uploads landing in the active project, kernel WORK_DIR injection).

    Moved here from core.config (burn-down #1): config is a foundational leaf and
    must not import projects. This is the natural home — it just wraps current()."""
    try:
        return current() or "_workspace"
    except Exception:  # noqa: BLE001
        return "_workspace"


def ensure_opened(pid: str) -> None:
    """Idempotent per-process project open WITHOUT mutating the global DB.

    Call INSIDE `with bind(pid):` (the per-request ASGI middleware does) so
    init_db()/reap operate on the context-bound DB. First time only: create
    the project's tables and reap its previous-process stale turns (#14's
    first-open memo). Subsequent calls are a set-membership check — this is the
    request hot path, so it must stay cheap. No-op in SINGLE mode."""
    if SINGLE or not pid or pid in _opened_pids:
        return
    _opened_pids.add(pid)
    init_db()
    if pid not in _reaped_pids:
        _reaped_pids.add(pid)
        # See set_current: the turn reaper subscribes to this event.
        try:
            from core.hooks.dispatcher import dispatch
            dispatch("on_project_first_open", {"pid": pid})
        except Exception:  # noqa: BLE001
            pass
    # Phase 2 (modularity_audit2 §2D): backfill typed derivations for
    # pre-provenance entities so old results show their origin when the scientist
    # returns. Here (not set_current): this runs inside `with bind(pid)`, so
    # _conn() is the project's DB, and once per pid per process (the _opened_pids
    # guard above). Idempotent + guarded — must never block the open.
    try:
        from core.graph.derivation_backfill import backfill_derivations
        backfill_derivations()
    except Exception:  # noqa: BLE001
        pass


@contextlib.contextmanager
def bind(pid: str | None):
    """Bind `pid` as the active project for the CURRENT execution context
    (asyncio task / thread) ONLY — isolated from the process-global
    set_current(). Both projects.current() and the DB connection (_conn via
    bind_active_db) resolve to `pid` inside the block.

    Background turn tasks (turn_executor._drain) wrap their whole run in this
    so a concurrent request that repoints the process-global DB — e.g. the
    frontend polling another open project's active-turn endpoint — can't swap
    the database out from under a running turn. That race silently corrupted
    turn history in 2026-06 (a turn read another project's messages mid-loop,
    lost the user's instruction, and produced a generic reply).

    No-op in SINGLE/test-harness mode (the harness owns DB_PATH) or when `pid`
    is falsy (no project to bind — leave the global fallback in place)."""
    if SINGLE or not pid:
        yield pid
        return
    tok_pid = _active_pid.set(pid)
    tok_db = _schema_mod.bind_active_db(_db_file(pid))
    try:
        yield pid
    finally:
        _schema_mod.reset_active_db(tok_db)
        _active_pid.reset(tok_pid)


def _touch(pid: str) -> None:
    with _locked_registry() as reg:
        for p in reg:
            if p["id"] == pid:
                p["last_touched"] = _now()


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
    pid = "prj_" + uuid.uuid4().hex[:8]
    entry = {"id": pid, "name": (name or "Untitled project").strip()[:80],
             "created_at": _now(), "last_touched": _now()}
    with _locked_registry() as reg:
        reg.append(entry)
    set_current(pid)
    update_entity("workspace", title=entry["name"])  # in-project title = project name
    _emit_project_meta(pid)
    return {**entry, "current": True, "counts": {}}


def rename_project(pid: str, name: str) -> None:
    if SINGLE:
        return
    with _locked_registry() as reg:
        for p in reg:
            if p["id"] == pid:
                p["name"] = (name or p["name"]).strip()[:80]
    _emit_project_meta(pid)


def project_model(pid: str) -> str:
    """The per-project LLM model the user selected (Settings → LLM), or "" if
    none. Stored on the registry entry; the chat loop resolves it via
    config.current_model_for_project(). SINGLE mode has no registry → "" (falls
    through to the global/bundle default)."""
    if SINGLE or not pid:
        return ""
    for p in _load():
        if p.get("id") == pid:
            return (p.get("model") or "").strip()
    return ""


def set_project_model(pid: str, model: str) -> None:
    """Pin (or clear, if `model` is falsy) the project's LLM model on its
    registry entry. Takes effect on the next turn (resolution is live)."""
    if SINGLE:
        return
    with _locked_registry() as reg:
        for p in reg:
            if p["id"] == pid:
                if (model or "").strip():
                    p["model"] = model.strip()
                else:
                    p.pop("model", None)
                break
    _emit_project_meta(pid)


def delete_project(pid: str) -> None:
    if SINGLE:
        return
    with _locked_registry() as reg:
        reg[:] = [p for p in reg if p["id"] != pid]
        survivors = list(reg)
    f = _db_file(pid)
    if f.exists():
        f.unlink()
    if _state["current"] == pid:
        if survivors:
            set_current(survivors[-1]["id"])
        else:
            _park_scratch()       # true empty state — no phantom project


# ─── Recovery archive emit ────────────────────────────────────────────────
def _emit_project_meta(pid: str) -> None:
    """Best-effort: enqueue a ProjectMetaChanged event so the FS recovery
    archive (misc/recovery.md) mirrors registry + workspace-entity state.
    Failures swallowed — DB is authoritative."""
    try:
        from core.recovery import get_scribe, ProjectMetaChanged  # noqa: PLC0415
        row = next((p for p in _load() if p["id"] == pid), None)
        if not row:
            return
        ws = None
        try:
            from core.graph.entities import get_entity   # noqa: PLC0415
            ws = get_entity("workspace")
        except Exception:
            pass
        get_scribe().enqueue(ProjectMetaChanged(pid=pid, payload={
            "registry": row,
            "project_entity": ws,
        }))
    except Exception:
        pass
