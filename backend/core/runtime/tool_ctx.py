"""Per-call context propagation for in-process MCP handlers (Phase 6.C).

Bio tools take a `ctx` dict carrying runtime objects: cancel_token (a
threading-aware token), progress_q (threading.Queue), active_tools (the
turn's enabled tool list, drives skill-tool linkage), sess (a Jupyter
kernel session), run_id, thread_id, project_id, etc.

ContextVars don't propagate across FastMCP's per-request task boundary
on the server side of the memory transport (each request spawns a fresh
asyncio task on the server's tree, which inherits the SERVER's context
not the CLIENT's). So we use a thread-safe ctx-store keyed by id, with
the id injected as a hidden MCP argument (`aba_ctx_id`).

Flow per tool call:

  dispatcher                          aba_core handler
  ----------                          ----------------
  cid = stash_ctx(ctx)                cx = peek_ctx(args['aba_ctx_id'])
  args['aba_ctx_id'] = cid           # use cx['cancel_token'] etc.
  mcp_call(...)
  pop_ctx(cid)  # always (finally)

Stash returns '' for empty/None ctx — no allocation, no store entry.
Pop is unconditional (`finally` block in the dispatcher) so a crashed
handler can't leak ctx entries. Peek lets a handler read ctx multiple
times during one execution without consuming it.

Thread-safe via a single global lock — tool dispatch frequency is low
(~10/turn) so lock contention is a non-issue.
"""
from __future__ import annotations
import threading
import uuid
from typing import Optional


_STORE: dict[str, dict] = {}
_LOCK = threading.Lock()


def stash_ctx(ctx: Optional[dict]) -> str:
    """Stash a per-call ctx, return the retrieval id. Empty/None ctx
    returns ''; the handler treats that as 'no context, all keys
    default'. Caller MUST pop after the call settles (use finally)."""
    if not ctx:
        return ""
    cid = uuid.uuid4().hex
    with _LOCK:
        _STORE[cid] = ctx
    return cid


def peek_ctx(cid: Optional[str]) -> dict:
    """Look up a stashed ctx without removing it. Returns {} for
    empty/unknown id — handlers can call defensively without
    null-checking the id first."""
    if not cid:
        return {}
    with _LOCK:
        return _STORE.get(cid) or {}


def pop_ctx(cid: Optional[str]) -> dict:
    """Remove a stashed ctx and return what was there. Idempotent —
    safe to call multiple times with the same id. Returns {} when
    the id wasn't in the store (already popped, never stashed, or
    empty)."""
    if not cid:
        return {}
    with _LOCK:
        return _STORE.pop(cid, {}) or {}


from contextlib import contextmanager  # noqa: E402


@contextmanager
def in_tool_ctx(aba_ctx_id: Optional[str]):
    """Peek the stashed ctx and bind any thread-local state derived
    from it (today: the progress sink) for the duration of the with
    block. Restores on exit so we don't leak the binding into the
    next call on the same gateway thread.

    Why this exists: when the bio dispatcher routes a tool to aba_core,
    the handler runs on the gateway's background asyncio thread, NOT
    on the worker thread that bound the progress sink. Without rebinding
    here, progress.emit() calls from deep inside long-running tools
    (ensure_capability installs, run_python/run_r kernels, nextflow)
    are no-ops and the chat goes silent during multi-minute work.

    Handlers that don't emit progress can still use peek_ctx directly
    — this CM is a superset, useful when you don't want to think
    about whether your handler might trigger emit() somewhere
    transitively."""
    from core.runtime import progress as _progress
    ctx = peek_ctx(aba_ctx_id)
    prev_q = _progress.current_sink()
    pq = ctx.get("progress_q")
    if pq is not None:
        _progress.set_sink(pq)
    try:
        yield ctx
    finally:
        if pq is not None:
            if prev_q is None:
                _progress.clear_sink()
            else:
                _progress.set_sink(prev_q)


def _reset_for_testing() -> None:
    """Clear the store between tests."""
    with _LOCK:
        _STORE.clear()


def _size_for_testing() -> int:
    """Inspect the store size — tests use this to assert no leaks."""
    with _LOCK:
        return len(_STORE)
