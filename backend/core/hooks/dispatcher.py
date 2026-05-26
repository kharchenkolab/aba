"""Named pre/post hook dispatcher.

Arch3_plan.md Pass D. Content registers handlers at import time:
    register('on_post_tool', _my_handler)
Core dispatches at named points:
    dispatch('on_post_tool', ctx)

Handlers run synchronously, ordered by `priority` (low first). Errors
are logged and swallowed — one bad hook must not break the agent loop.

Events used today (the names matter — handlers depend on them):
    on_post_tool   — fired after each tool result is appended; ctx has
                     tool_name, tool_input, result_obj, focus_entity_id,
                     analysis_ctx, thread_id, new_entities (handlers may
                     append)
    on_stop        — fired after the agent loop exits; ctx has session_id,
                     focus_entity_type, total_tool_calls, history,
                     thread_id, focus_entity_id, suggestion (handler may
                     set)

Adding a new event = pick a name and document it here.
"""
from __future__ import annotations
import logging
from typing import Callable

log = logging.getLogger(__name__)

HookHandler = Callable[[dict], None]
_HANDLERS: dict[str, list[tuple[int, HookHandler]]] = {}


def register(event: str, handler: HookHandler, priority: int = 0) -> None:
    """Register `handler` to fire when `dispatch(event, ctx)` is called.
    Lower priority numbers run first; same-priority handlers run in
    registration order."""
    _HANDLERS.setdefault(event, []).append((priority, handler))
    _HANDLERS[event].sort(key=lambda x: x[0])


def dispatch(event: str, ctx: dict) -> None:
    """Fire every handler registered for `event`, passing the shared ctx.
    Handlers may mutate ctx to communicate back to the caller (e.g. append
    to ctx['new_entities']). Exceptions are logged, never raised — one
    handler cannot break the loop."""
    for _, handler in _HANDLERS.get(event, ()):
        try:
            handler(ctx)
        except Exception:
            log.exception("hook handler %s for event %s raised", handler, event)


def registered_events() -> dict[str, int]:
    """Diagnostic: how many handlers are registered per event."""
    return {ev: len(handlers) for ev, handlers in _HANDLERS.items()}
