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


def _reset_for_testing() -> None:
    """Clear the store between tests."""
    with _LOCK:
        _STORE.clear()


def _size_for_testing() -> int:
    """Inspect the store size — tests use this to assert no leaks."""
    with _LOCK:
        return len(_STORE)
