"""Sync tool bodies run on worker threads, never on the gateway loop.

The incident (2026-08-28, root cause of the thread-interference class): every
aba_core tool is a plain `def`, and fastmcp's dispatch runs a sync tool INLINE
on whatever loop serves the request (`func_metadata.call_fn_with_arg_validation`
— `return fn(**args)` on the async path). All in-process servers share the ONE
gateway loop, so a single long tool body — a 90 s package install, a minutes-
long analysis block — froze dispatch of every other tool call in the process:
the sibling's turn waited not for the install but for the installing FUNCTION
to return (observed live as the sibling unblocking ~9 s after the install
itself finished, at tool-body end). Stack-dump proof chain in
regtest/FINDINGS.md 2026-08-28.

The policy: after a server's tools are registered, every SYNC tool's `fn` is
replaced with an async wrapper that runs the original body via
`anyio.to_thread.run_sync` on a dedicated capacity limiter. The loop then only
orchestrates. The calling CONTRACT is untouched BY CONSTRUCTION: fastmcp
freezes each tool's schema (`parameters` / `fn_metadata`) at registration from
the ORIGINAL function, and dispatch reads only `tool.fn` / `tool.is_async` at
call time — the two attributes swapped here. Async tools pass through
unchanged (they are presumed to yield; a blocking async tool is a bug at its
own doorstep).

Cancellation: `abandon_on_cancel=True` — Stop cancels the awaiting task
promptly while the body finishes on its worker. That matches the pre-fix
surface (a running sync body could never be interrupted mid-flight either;
cooperative interruption stays the cancel_token's job) without letting an
abandoned body block the loop's task.

One owner: `in_process.InProcessServerHandle.connect` applies this to every
factory-built server on every (re)connect. Do not wrap at call sites.
"""
from __future__ import annotations

import functools
import threading

import anyio
import anyio.to_thread

# Worker capacity for concurrently-executing tool bodies. Sized well above any
# plausible number of simultaneously-active turns; the pre-fix capacity was
# effectively ONE (the loop). Created lazily on first use because anyio
# primitives bind to a running backend.
_TOOL_WORKERS = 64
_limiter_box: list = []
_limiter_lock = threading.Lock()


def _limiter() -> anyio.CapacityLimiter:
    with _limiter_lock:
        if not _limiter_box:
            _limiter_box.append(anyio.CapacityLimiter(_TOOL_WORKERS))
        return _limiter_box[0]


def _offloaded(fn):
    @functools.wraps(fn)
    async def _run(**kwargs):
        return await anyio.to_thread.run_sync(
            functools.partial(fn, **kwargs),
            abandon_on_cancel=True, limiter=_limiter())
    return _run


def offload_sync_tools(server) -> int:
    """Rewrite every SYNC tool on a FastMCP `server` to execute off-loop.

    Returns the number of tools wrapped. Raises if the fastmcp internals this
    relies on (`_tool_manager._tools`, `Tool.fn` / `Tool.is_async`) are not
    where this version keeps them — a silent no-op here would resurrect the
    one-slow-tool-freezes-everything bug invisibly, so failing the connect is
    the safer wrong."""
    tools = server._tool_manager._tools          # AttributeError = fail loud
    n = 0
    for tool in tools.values():
        if tool.is_async:
            continue
        tool.fn = _offloaded(tool.fn)
        tool.is_async = True
        n += 1
    return n
