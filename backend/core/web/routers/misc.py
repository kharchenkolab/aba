"""Small platform routes with no natural group — bundle snapshot, entity-type
catalog, activity feed, the out-of-band notifications SSE channel, and health.
Extracted from main.py (Item 2A.3). Domain-neutral (core.bundle / core.entity_types
/ core.graph.audit / core.runtime.notifications)."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from core.graph.audit import list_events

router = APIRouter()


@router.get("/api/bundle/state")
def bundle_state(reload: bool = False):
    """Active EffectiveBundle snapshot for admin/diagnostic UI.

    `?reload=true` forces a re-resolution (drops the module-level cache)
    so admins can pick up a freshly-edited site.yaml or env change without
    restarting the backend."""
    from core.bundle.active import get_bundle, get_resolution, reload_bundle
    from core.bundle.cli import _state_dict
    eb = reload_bundle() if reload else get_bundle()
    return _state_dict(get_resolution(), eb)


@router.get("/api/entity-types")
def entity_types_catalog():
    """The declarative entity-type catalog (Phase 4.6 of misc/
    phase4_entity_types.md). One entry per type with the metadata the
    frontend needs to dispatch (display name, icon, ui.panel, hidden
    flag, creation gestures). The frontend fetches this once on app
    load + caches; entity-aware components look up by type instead of
    hardcoded switch/case. Domain-neutral — the platform serves whatever
    the loaded YAMLs declare."""
    from core.entity_types import list_types
    out: list[dict] = []
    for t in list_types():
        out.append({
            "name": t.name,
            "display": t.display,
            "icon": t.icon,
            "hidden": t.hidden,
            "category": t.category,
            "status_states": list(t.status_model.get("states") or []),
            "ui": t.ui,
            "creation": t.creation,
            # advisors block (incl. on_focus_auto flag) — drives
            # AdvisorStrip's auto-advise-on-focus behaviour on the frontend
            # instead of hardcoding "dataset || narrative" there.
            "advisors": t.advisors,
        })
    return out


@router.get("/api/events")
def events_list(limit: int = 50, offset: int = 0):
    """Activity / audit feed (newest first)."""
    return list_events(limit=limit, offset=offset)


@router.get("/api/notifications")
async def notifications_stream():
    """Global SSE channel for OUT-OF-BAND events (caption ready, background
    job done, entity updated, …) — things that happen outside the chat
    turn lifecycle. Frontend opens this once on app mount and refreshes
    on relevant events instead of guessing refresh intervals.

    Event shape: `{"type": "entity_updated", "entity_id": "...", "reason": "..."}`.
    A "hello" event fires on connect so the client knows the stream is live.
    """
    from core.runtime import notifications as _notif

    async def gen():
        q = _notif.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies / load balancers from closing
                    # the idle connection.
                    yield ": heartbeat\n\n"
        finally:
            _notif.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/api/health")
def health():
    return {"ok": True}
