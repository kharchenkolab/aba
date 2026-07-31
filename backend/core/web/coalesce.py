"""Single-flight + bounded occupancy for expensive sync route computations.

Why this exists (the durable-panel convoy, 2026-07): a polled route whose
computation makes many serialized substrate queries holds a threadpool worker
for the whole wait. FastAPI runs sync-def routes AND run_in_threadpool on the
SAME anyio pool (default 40 tokens) — so N pollers × one slow computation
parked N workers, and once ~40 were parked every OTHER sync route (images,
messages, entities) had no thread to run on: the UI didn't get slow, it
stopped, and it all came back at once when the agent released the substrate.

Two guarantees, and deliberately nothing more:
  - single-flight: concurrent calls with the same key share ONE computation.
    NOT a cache — the moment a flight resolves it is forgotten; the next
    call recomputes. Nothing can go stale, nothing to invalidate.
  - bounded occupancy: at most `max_concurrent` computations touch the
    threadpool at once, so this route class can never hold more than that
    many of the 40 tokens no matter how many pollers exist. Everyone else
    waits in the event loop, holding no thread.

The async route that uses this must keep sync work (DB reads, filesystem)
INSIDE the coalesced fn — an async def body runs on the event loop, where a
blocking call stalls every request in the process.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool


def _observe(task: asyncio.Task) -> None:
    # Every awaiter may have disconnected before the flight failed; retrieve
    # the exception so asyncio doesn't log "exception was never retrieved".
    if not task.cancelled():
        task.exception()


class Coalescer:
    """Per-key single-flight over a bounded threadpool occupancy.

    Event-loop-confined: `get` must be awaited from route handlers of one
    running app (all mutation of `_inflight` happens on the loop, so no lock).
    """

    def __init__(self, max_concurrent: int = 2):
        # 2, not 1: one long-tail flight must not gate a fresh key entirely;
        # more buys nothing against a single-lane substrate store.
        self._inflight: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(max_concurrent)

    async def get(self, key: str, fn: Callable[[], Any]) -> Any:
        """Return fn()'s result, sharing one in-flight computation per key.

        The computation runs as a detached task: a caller disconnecting (or
        being cancelled) never cancels the flight other callers are awaiting
        — `shield` decouples each awaiter from the shared task. Exceptions
        (including HTTPException from inside fn) propagate to every awaiter.
        """
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.get_running_loop().create_task(self._run(key, fn))
            task.add_done_callback(_observe)
            self._inflight[key] = task
        return await asyncio.shield(task)

    async def _run(self, key: str, fn: Callable[[], Any]) -> Any:
        try:
            async with self._sem:
                return await run_in_threadpool(fn)
        finally:
            # Unregister BEFORE the task resolves (finally runs inside the
            # coroutine, synchronously ahead of the done-transition): a call
            # arriving after resolution must start a FRESH flight — attaching
            # it to a done task would silently return a stale result, turning
            # single-flight into an unbounded-age cache.
            self._inflight.pop(key, None)

    def inflight(self) -> int:
        """Observability: number of open flights (for tests/diagnostics)."""
        return len(self._inflight)
