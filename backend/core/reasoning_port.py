"""Reasoning-plane re-entry port — the Compute→Reasoning inversion (modularity_audit3 Item 1).

`core.jobs` must NOT import the orchestrator (`guide`). A finished background job re-enters
the agent loop through this registered callback instead: the reasoning plane (guide) registers
its continuation handler at import/startup, and `core.jobs.continuation` calls the port.

Unlike `core/services` (best-effort, nullable — a missing content pack must never break core),
this callback is **mandatory**: a dropped continuation breaks the deferred-turn contract (a
finished job would leave its plan un-resumed forever). So an unregistered port is a LOUD wiring
error, never a silent no-op. In the real app the guide registers it at import (imported by
`main.py` before `startup()` → before reconcile/worker/poll fire any continuation).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

# Signature: (cont_text: str, *, focus_entity_id: str, thread_id: str, run_id: str) -> body_gen
# where body_gen is the turn's async generator (what guide.stream_response returns).
_CONTINUATION: Optional[Callable[..., Any]] = None


def register_continuation(fn: Callable[..., Any]) -> None:
    """Register the orchestrator's continuation handler. Called once at startup by the
    reasoning plane. Idempotent — re-registration replaces (matches core/services)."""
    global _CONTINUATION
    _CONTINUATION = fn


def is_registered() -> bool:
    """True once the reasoning plane has registered its handler. Usable as a startup assertion."""
    return _CONTINUATION is not None


def run_continuation(cont_text: str, *, focus_entity_id: str, thread_id: str, run_id: str) -> Any:
    """Re-enter the agent loop for a completed job's continuation; returns the turn body
    generator (the caller wires it into the turn executor). Raises if unregistered — a
    finished job MUST NOT be silently left without its plan resuming."""
    if _CONTINUATION is None:
        raise RuntimeError(
            "reasoning continuation port is not registered: the orchestrator must call "
            "reasoning_port.register_continuation() at startup, else a finished background "
            "job cannot resume its plan (deferred-turn contract violation)."
        )
    return _CONTINUATION(cont_text, focus_entity_id=focus_entity_id,
                         thread_id=thread_id, run_id=run_id)
