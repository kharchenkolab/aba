"""Tool progress channel — live status for long synchronous tool calls.

Installs (pip/conda/R), kernel execution, and nextflow can run for minutes
inside a single tool dispatch. The dispatch runs in a worker thread
(guide.py: run_in_executor), so deep tool code can push phase lines onto a
thread-local sink that the async Guide loop drains and streams to the client
as `tool_progress` SSE events — turning an opaque wait into "Installing Seurat
(CRAN/PPM)… ▸ recompiling from source".

Thread-local (not a ContextVar): one tool runs at a time per turn, in one
worker thread, and contextvars don't propagate into run_in_executor threads —
so execute_tool sets the sink at the top of the worker thread and everything it
calls in that thread can `emit()`.
"""
from __future__ import annotations
import threading
from typing import Optional

_local = threading.local()


def set_sink(q) -> None:
    """Bind a thread-safe queue (queue.Queue) as this thread's progress sink."""
    _local.q = q


def clear_sink() -> None:
    _local.q = None


def emit(message: str, *, phase: Optional[str] = None) -> None:
    """Push a progress line for the current tool call. No-op if no sink is bound
    (e.g. tests, background jobs, or callers outside a streamed dispatch)."""
    q = getattr(_local, "q", None)
    if q is None:
        return
    try:
        q.put_nowait({"message": str(message), "phase": phase})
    except Exception:  # noqa: BLE001 — progress must never break the tool
        pass
