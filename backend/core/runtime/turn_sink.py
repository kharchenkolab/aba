"""Per-Turn event log + process-global registry.

C-0 of the durable-turns redesign (misc/durable_turns_plan.md). Today the
agent loop in `guide.py:stream_response` is a generator whose lifetime is
tied to the HTTP request — when the client disconnects (tab switch,
network blip), the generator is cancelled mid-tool-await and the Turn is
stranded in `executing_tools` forever.

This module is the foundation. Every event the loop currently yields as
SSE is ALSO pushed onto a per-Turn `TurnSink`. The sink keeps a bounded
in-memory tail (so a reattaching client can replay recent events) and
exposes a process-global registry so the frontend can ask
"is there an in-flight Turn for thread X right now?" — which restores the
Stop button after a reload even without the full reattachable-stream
machinery (that's C-1).

Stays additive in C-0: `stream_response` continues to yield SSE chunks
as before; this module is a side-channel. C-1 will flip the executor to
emit ONLY through the sink and serve the SSE from the sink's subscribers.
"""
from __future__ import annotations
import asyncio
from collections import deque
from typing import Optional


MAX_TAIL = 1000          # events kept in the in-memory ring per Turn
SUB_QUEUE_MAX = 1000     # per-subscriber inbox; oldest dropped on overflow


class TurnSink:
    """Append-only event log for one Turn. Producers (the agent loop)
    call `push(payload)`; subscribers (SSE reattach in C-1) consume
    via `subscribe()` / `replay_since(seq)`.

    Cheap. No I/O in C-0 — purely in-memory. Disk replay arrives in C-2.
    """

    __slots__ = ("run_id", "thread_id", "started_at", "_seq",
                 "_tail", "_subs", "_closed")

    def __init__(self, run_id: str, thread_id: str | None, started_at: str):
        self.run_id = run_id
        self.thread_id = thread_id
        self.started_at = started_at
        self._seq = 0
        self._tail: deque[tuple[int, dict]] = deque(maxlen=MAX_TAIL)
        self._subs: set[asyncio.Queue] = set()
        self._closed = False

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    def push(self, payload: dict) -> int:
        """Append an event. Returns the new seq. Best-effort fan-out to
        live subscribers; a slow subscriber drops its oldest. Called from
        the agent loop (on the event loop thread)."""
        self._seq += 1
        seq = self._seq
        rec = (seq, payload)
        self._tail.append(rec)
        for q in list(self._subs):
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                # Slow subscriber. Drop the oldest in their queue to make
                # room; they'll see a gap and (in C-1) can reattach with
                # ?since=<lastSeq> to backfill. C-0 just drops silently.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(rec)
                except asyncio.QueueFull:
                    pass
        return seq

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUB_QUEUE_MAX)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def replay_since(self, since: int) -> list[tuple[int, dict]]:
        """Return all in-memory events with seq > since, in order. If
        `since` is older than the in-memory tail's start (i.e. the
        client missed too much), returns the full tail — the gap
        signals "you're behind." Disk-replay backfill is C-2."""
        if since >= self._seq:
            return []
        # Tail is bounded; scan is O(MAX_TAIL).
        return [(s, p) for (s, p) in self._tail if s > since]

    def close(self) -> None:
        """Mark the sink closed. Future pushes are no-ops; subscribers
        get sentinel `None` so their generators can exit cleanly."""
        if self._closed:
            return
        self._closed = True
        for q in list(self._subs):
            try:
                q.put_nowait((self._seq, None))   # sentinel
            except asyncio.QueueFull:
                pass


# ---- Process-global registry ----------------------------------------

_REGISTRY: dict[str, TurnSink] = {}


def create(run_id: str, thread_id: str | None, started_at: str) -> TurnSink:
    """Allocate + register a sink for `run_id`. Idempotent — a second
    call for the same run_id returns the existing sink (which is what
    you want if a retry re-enters the same Turn)."""
    existing = _REGISTRY.get(run_id)
    if existing is not None:
        return existing
    s = TurnSink(run_id, thread_id, started_at)
    _REGISTRY[run_id] = s
    return s


def get(run_id: str) -> Optional[TurnSink]:
    return _REGISTRY.get(run_id)


def release(run_id: str) -> None:
    """Close + drop from the registry. Called from the agent loop's
    finally block, after the final `done` event has been pushed."""
    s = _REGISTRY.pop(run_id, None)
    if s is not None:
        s.close()


def active_for_thread(thread_id: str) -> Optional[TurnSink]:
    """Most-recent live sink whose thread_id matches. Used by the
    `/api/threads/{tid}/active-turn` endpoint so the frontend can
    surface a Stop button after a reload."""
    if not thread_id:
        return None
    # Most recently started wins — if more than one is somehow live
    # (e.g. duplicate POST), the latest is the one the user cares about.
    candidates = [s for s in _REGISTRY.values()
                  if s.thread_id == thread_id and not s.closed]
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.started_at, reverse=True)
    return candidates[0]


def active_ids() -> list[str]:
    return [rid for rid, s in _REGISTRY.items() if not s.closed]
