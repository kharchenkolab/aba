"""FastAPI dependencies — domain-neutral.

`require_project` is the canonical per-request project pin. Use it via
`Depends` on any endpoint that touches project-scoped data (entities,
threads, runs, results, claims, datasets, files, jobs, etc.):

    @app.get("/api/entities/{eid}")
    def get(eid: str, pid: str = Depends(require_project)):
        ...

The dep is *permissive* about input source: it accepts `?project_id=`
in the query, or an `X-Project-Id` header. If neither is provided but
the process-global project is set (e.g. via `POST /api/projects/{pid}/
open`), the dep returns that. The only failure mode is "no source and
no global", which yields HTTP 412 — the symptom of the silent-misroute
bug the 2026-06-02 audit was chasing.

Why a dep and not the older `_require_project_context()` function call:
- Endpoints can't *forget* to call it — it's in the signature.
- A future endpoint that misses `Depends(require_project)` is visible
  in code review (vs. an omitted function call which is invisible).
- The dep returns the pid; handlers don't have to re-read it.

Body-sourced project_id (chat: `req.project_id` in the request body)
still uses the function form `_require_project_context()` defined in
main.py — that one is a thin wrapper around the same primitive below.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Header

from core import projects as _projects


def _pin_or_412(pid: str | None) -> str:
    """The canonical primitive both the Depends form (this module) and the
    body-sourced form (`_require_project_context` in main.py) call into.

    Permissive: missing pid + set global → no-op, returns global pid.
    Strict only when both pid is absent AND global is unset.
    """
    if pid:
        if _projects.current() != pid:
            _projects.set_current(pid)
        return pid
    cur = _projects.current()
    if cur is None:
        raise HTTPException(
            412,
            "no project context — pass ?project_id= or the X-Project-Id "
            "header, or call POST /api/projects/{pid}/open first",
        )
    return cur


def require_project(
    project_id: str | None = Query(default=None),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> str:
    """FastAPI dependency: pin the project per-request. Returns the pid. Also sets
    the ambient actor to human:local — a direct call to a gated HTTP route is a
    human action (the agent acts via the turn loop / MCP tools, not these)."""
    pid = _pin_or_412(project_id or x_project_id)
    try:
        from core.graph.actor import set_actor
        from core.graph.derivation import human_actor
        set_actor(human_actor())
    except Exception:  # noqa: BLE001 — actor attribution must never break a request
        pass
    return pid


def optional_project(
    project_id: str | None = Query(default=None),
    x_project_id: str | None = Header(default=None, alias="X-Project-Id"),
) -> str | None:
    """Like require_project, but TOLERANT: returns the pinned pid, or None when there's
    no project context (instead of 412). For install-wide settings that are meaningful
    with no project open (e.g. the default model). Pins the pid when given and sets the
    human actor, same as require_project."""
    pid = project_id or x_project_id
    if pid:
        if _projects.current() != pid:
            _projects.set_current(pid)
        resolved: str | None = pid
    else:
        resolved = _projects.current()   # may be None on a fresh install
    try:
        from core.graph.actor import set_actor
        from core.graph.derivation import human_actor
        set_actor(human_actor())
    except Exception:  # noqa: BLE001 — actor attribution must never break a request
        pass
    return resolved


def require_project_context(project_id: str | None) -> str:
    """Pin the project per-request for handlers that take project_id in the REQUEST
    BODY (chat/resume) — the ASGI middleware can't safely parse the body. For
    query/header sources, prefer Depends(require_project). Both share `_pin_or_412`.

    Returns the RESOLVED pid (body value, or the global fallback when the body omits
    it) so callers can bind it to their context — see chat()/#18. (Moved from main.py
    in Item 2A.3 so router modules share it without importing up from main.)"""
    return _pin_or_412(project_id)


__all__ = ["require_project", "optional_project", "require_project_context", "_pin_or_412"]
