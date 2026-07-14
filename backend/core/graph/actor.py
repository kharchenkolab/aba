"""Ambient actor for entity creation (modularity_audit2 §Phase 2B).

Who is acting *right now* — set at the boundaries, read by `create_entity` as the
default `actor` when a caller doesn't pass one explicitly. ContextVar-based, so it
follows the request/turn task but does NOT cross FastMCP's tool-dispatch boundary
(agent-tool handlers resolve their own actor from peek_ctx — see tool_ctx.py).

Wiring:
  - HTTP mutating routes (human actions): require_project -> human:local
  - agent turn loop:                       acting_as(agent:<run_id>)
  - exec-born materialize:                 passes actor=agent:<run_id> explicitly
    (it holds the exec record's run_id, which the contextvar can't reach on the
    gateway thread).
"""
from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Optional

_current_actor: ContextVar[Optional[str]] = ContextVar("aba_current_actor", default=None)


def set_actor(actor: Optional[str]) -> None:
    """Set the ambient actor for the rest of this task (request/turn)."""
    _current_actor.set(actor)


def current_actor() -> Optional[str]:
    """The ambient actor, or None if nothing is acting (background/unknown)."""
    return _current_actor.get()


@contextlib.contextmanager
def acting_as(actor: Optional[str]):
    """Bind the ambient actor for the duration of the block, then restore."""
    token = _current_actor.set(actor)
    try:
        yield
    finally:
        _current_actor.reset(token)
