"""Project-context pinning middleware (#17) — extracted from main.py (Item 2A.2).

Every request carrying `?project_id=<pid>` or `X-Project-Id: <pid>` is pinned to
that project for the request's duration by BINDING A CONTEXTVAR (projects.bind) —
not by mutating the process-global DB_PATH. This isolates concurrent requests for
different projects from each other (the old version's set_current() mutated a
shared global, so two tabs — or the frontend polling two open projects — raced;
that same global is what corrupted streaming turns in #15).

It MUST be a pure-ASGI middleware, not @app.middleware("http")
(BaseHTTPMiddleware): the latter runs the endpoint in a separate anyio task, so a
contextvar set in it does NOT propagate to the handler. A pure-ASGI middleware
runs the app in the SAME context, and Starlette's threadpool for sync `def`
handlers copies that context — so both async and sync handlers see the binding.
`_conn()` already prefers the bound path (#15).

Body-sourced project_id (chat: req.project_id in the JSON body) is NOT visible in
the ASGI scope, so chat still pins via _require_project_context in its handler;
its background turn then re-binds the captured project (#15).

Domain-neutral (only core.projects) — lives under core/web so the seam holds.
"""
from __future__ import annotations


def _pid_from_scope(scope) -> str | None:
    from urllib.parse import parse_qs
    qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
    pid = (qs.get("project_id") or [None])[0]
    if pid:
        return pid
    for k, v in scope.get("headers") or []:
        if k == b"x-project-id":
            return v.decode("latin-1") or None
    return None


class ProjectPinMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        from core import projects as _projects
        pid = _pid_from_scope(scope)
        if not pid or _projects.SINGLE:
            return await self.app(scope, receive, send)
        with _projects.bind(pid):
            _projects.ensure_opened(pid)
            await self.app(scope, receive, send)
