"""Per-Turn event log + process-global registry.

Foundation for the durable-turns redesign (misc/durable_turns_plan.md).
Before this, the agent loop in `guide.py:stream_response` was a generator
whose lifetime was tied to the HTTP request — client disconnect (tab
switch, network blip) cancelled the generator mid-tool-await and the
Turn was stranded in `executing_tools` forever.

C-1 flips the architecture: the agent loop runs as a background task
that emits via `TurnSink.push(obj)`. The SSE response is just a
subscriber on the sink — client disconnect unsubscribes but the task
keeps running. A reconnecting client opens a new subscription via
`GET /api/turns/{rid}/stream?since=<seq>` and the sink replays missed
events from its in-memory tail.

Sink lifecycle:
  * `create(run_id, ...)` — allocate + register
  * `push(obj)` — append + fan out to subscribers
  * `close(run_id)` — done streaming; subscribers get a `None` sentinel
    so their generators exit cleanly. Sink stays in the registry (with
    its in-memory tail) so a late reconnect can still replay.
  * `evict(run_id)` — actually remove from registry. C-2 will sweep on
    TTL; today nothing calls it (memory leak per turn is bounded — each
    closed sink holds ~MAX_TAIL events, freed at process exit).
"""
from __future__ import annotations
import asyncio
import json
from collections import deque
from typing import AsyncGenerator, Optional


MAX_TAIL = 1000          # events kept in the in-memory ring per Turn
SUB_QUEUE_MAX = 1000     # per-subscriber inbox; oldest dropped on overflow
HEARTBEAT_SECONDS = 25   # SSE keepalive interval (vite/nginx idle-close guard)


class TurnSink:
    """Append-only event log for one Turn. Producers (the agent loop)
    call `push(payload)`; subscribers (SSE reattach in C-1) consume
    via `subscribe()` / `replay_since(seq)`.

    Cheap. No I/O in C-0 — purely in-memory. Disk replay arrives in C-2.
    """

    __slots__ = ("run_id", "thread_id", "started_at", "_seq",
                 "_tail", "_subs", "_closed", "_task")

    def __init__(self, run_id: str, thread_id: str | None, started_at: str):
        self.run_id = run_id
        self.thread_id = thread_id
        self.started_at = started_at
        self._seq = 0
        self._tail: deque[tuple[int, dict]] = deque(maxlen=MAX_TAIL)
        self._subs: set[asyncio.Queue] = set()
        self._closed = False
        self._task: Optional[asyncio.Task] = None   # set by turn_executor.start_turn

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


def close(run_id: str) -> None:
    """Mark the sink closed (sentinel to subscribers). Keep it in the
    registry so a late reconnect can still replay the in-memory tail.
    Called from the agent loop's finally block."""
    s = _REGISTRY.get(run_id)
    if s is not None:
        s.close()


def release(run_id: str) -> None:
    """Backwards-compat alias for the C-0 callsite. Now equivalent to
    `close()` — the sink stays alive in the registry post-close so a
    reconnecting client can replay. Use `evict()` for actual removal."""
    close(run_id)


def evict(run_id: str) -> None:
    """Remove the sink from the registry entirely. C-2 sweeps closed
    sinks on TTL; manual eviction is rarely needed."""
    _REGISTRY.pop(run_id, None)


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


# ---- SSE consumer -----------------------------------------------------

async def stream_from_sink(sink: TurnSink, *, since: int = 0
                           ) -> AsyncGenerator[str, None]:
    """Yield SSE wire frames from a sink: first replay any events with
    seq > since from the in-memory tail, then live-stream new events as
    the producer pushes them. Embeds `seq` into each event's payload so
    the client can persist `lastSeq` and request a reattach with
    `?since=<lastSeq>` on disconnect.

    Honors `sink.close()`: subscribers receive `(seq, None)` as a
    sentinel and this generator yields a final `done` event (if not
    already covered by an explicit `done` push from the producer) and
    returns cleanly.

    Heartbeats (`: keepalive\n\n`, an SSE comment line ignored by
    EventSource clients) keep proxies from idling the connection."""
    q = sink.subscribe()
    try:
        # 1. Replay any events with seq > since from the in-memory tail.
        # If the client missed events older than the tail's start, those
        # are lost (until C-2's disk replay) — but the gap is visible
        # via the seq jump, so a reattach with since=0 is always safe.
        for seq, payload in sink.replay_since(since):
            yield _format(seq, payload)

        # 2. Live-stream. Heartbeat on timeout to keep the connection
        # alive during quiet periods (e.g. mid-tool, no progress events).
        while True:
            try:
                seq, payload = await asyncio.wait_for(q.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if payload is None:
                # Sentinel — producer closed the sink. Done.
                return
            yield _format(seq, payload)
    finally:
        sink.unsubscribe(q)


def _format(seq: int, payload: dict) -> str:
    """Wire format: `data: {payload + seq}\n\n`. The seq lets clients
    persist their position and request `?since=<lastSeq>` on reattach."""
    obj = dict(payload)
    obj["seq"] = seq
    return f"data: {json.dumps(obj)}\n\n"
