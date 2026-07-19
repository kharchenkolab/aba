"""Per-Turn event log + process-global registry.

Foundation for the durable-turns redesign (misc/durable_turns_plan.md).
Before this, the agent loop in `guide.py:stream_response` was a generator
whose lifetime was tied to the HTTP request — client disconnect (tab
switch, network blip) cancelled the generator mid-tool-await and the
Turn was stranded in `executing_tools` forever.

C-1 flipped the architecture: the agent loop runs as a background task
that emits via `TurnSink.push(obj)`. The SSE response is just a
subscriber on the sink — client disconnect unsubscribes but the task
keeps running. A reconnecting client opens a new subscription via
`GET /api/turns/{rid}/stream?since=<seq>` and the sink replays missed
events from its in-memory tail.

C-2 (this commit) adds DISK persistence: every event is also
append-streamed to `RUNTIME_DIR/turn_events/<run_id>.jsonl`. Two payoffs:
  - Reattach after backend restart: the SSE endpoint can rehydrate
    the historic event stream from disk even after the in-memory sink
    is gone (returns a one-shot replay, no live stream).
  - In-memory tail overflow: if a slow / disconnected subscriber
    reattaches with `since=N` and `N` predates the in-memory tail
    (MAX_TAIL=1000), `replay_since` falls back to scanning the JSONL.

Sink lifecycle:
  * `create(run_id, ...)` — allocate + register; opens the JSONL file
  * `push(obj)` — append in-memory + write JSONL line + fan out
  * `close(run_id)` — done streaming; subscribers get a `None` sentinel
    so their generators exit cleanly. JSONL flushed + closed. Sink
    stays in the registry (with its in-memory tail) so a late reconnect
    can still replay without hitting disk.
  * `evict(run_id)` — remove from registry. C-2's TTL sweeper does this
    for closed sinks older than CLOSED_SINK_TTL_S; the JSONL file
    survives until TURN_EVENTS_TTL_S (longer) so post-restart reattach
    still works.
  * `rehydrate(run_id)` — read all events for a no-longer-in-registry
    Turn from disk. Returns a list; caller streams them out and closes.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import AsyncGenerator, Optional, IO


MAX_TAIL = 1000          # events kept in the in-memory ring per Turn
SUB_QUEUE_MAX = 1000     # per-subscriber inbox; oldest dropped on overflow
HEARTBEAT_SECONDS = 25   # SSE keepalive interval (vite/nginx idle-close guard)

# C-2 retention:
# - JSONL files: kept 7 days so a user resuming from a stale tab still
#   sees the full event stream replayed even though the agent loop is
#   long gone.
# - Closed sinks in the registry: kept 1h. After that the in-memory
#   tail goes; subsequent reattach falls back to the disk path.
TURN_EVENTS_TTL_S    = 7 * 24 * 3600
CLOSED_SINK_TTL_S    = 3600


def _turn_events_dir() -> Path:
    """Where per-Turn JSONLs live. Resolved lazily (defer to core.config
    so test isolation honoring ABA_RUNTIME_DIR works)."""
    from core.config import RUNTIME_DIR
    p = RUNTIME_DIR / "turn_events"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _jsonl_path(run_id: str) -> Path:
    return _turn_events_dir() / f"{run_id}.jsonl"


class TurnSink:
    """Append-only event log for one Turn. Producers (the agent loop)
    call `push(payload)`; subscribers (SSE reattach in C-1) consume
    via `subscribe()` / `replay_since(seq)`.

    Cheap. No I/O in C-0 — purely in-memory. Disk replay arrives in C-2.
    """

    __slots__ = ("run_id", "thread_id", "started_at", "_seq",
                 "_tail", "_subs", "_closed", "_closed_at", "_task",
                 "_jsonl_fh")

    def __init__(self, run_id: str, thread_id: str | None, started_at: str):
        self.run_id = run_id
        self.thread_id = thread_id
        self.started_at = started_at
        self._seq = 0
        self._tail: deque[tuple[int, dict]] = deque(maxlen=MAX_TAIL)
        self._subs: set[asyncio.Queue] = set()
        self._closed = False
        self._closed_at: Optional[float] = None
        self._task: Optional[asyncio.Task] = None   # set by turn_executor.start_turn
        # JSONL append handle. Opened lazily on first push (so a sink
        # that's allocated but never pushed to leaves no file behind).
        # Append mode: tolerates a pre-existing file (e.g. a same run_id
        # rerun on dev — unusual; gen_run_id is uuid-based) without
        # losing prior events.
        self._jsonl_fh: Optional[IO[str]] = None

    @property
    def last_seq(self) -> int:
        return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    def push(self, payload: dict) -> int:
        """Append an event. Returns the new seq. Best-effort fan-out to
        live subscribers; a slow subscriber drops its oldest. Also
        appends to the JSONL on disk for post-restart replay (C-2).

        Called from the agent loop (on the event loop thread)."""
        # Wire-contract conformance (core/runtime/wire.py) — warn-once,
        # never fatal: the transport is the last line of defence.
        from core.runtime import wire
        wire.check(payload, "turn")
        self._seq += 1
        seq = self._seq
        rec = (seq, payload)
        self._tail.append(rec)
        # C-2: persist to disk. Open lazily on first push so an unused
        # sink (allocated but never pushed) leaves no file behind.
        try:
            if self._jsonl_fh is None:
                self._jsonl_fh = open(_jsonl_path(self.run_id), "a",
                                      encoding="utf-8")
            self._jsonl_fh.write(json.dumps({"seq": seq, "payload": payload},
                                             default=str) + "\n")
            self._jsonl_fh.flush()
        except Exception:  # noqa: BLE001 — disk failure must never block dispatch
            pass    # in-memory tail + live subscribers still get the event
        for q in list(self._subs):
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                # Slow subscriber. Drop the oldest in their queue to make
                # room; they'll see a gap and (in C-1) can reattach with
                # ?since=<lastSeq> to backfill — disk replay covers the
                # gap if it falls outside the in-memory tail.
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
        """Return all events with seq > since, in order. Tries the
        in-memory tail first; if `since` predates the tail's earliest
        seq (the client missed more events than MAX_TAIL holds), falls
        back to scanning the JSONL on disk for the gap before stitching
        the rest from the tail."""
        if since >= self._seq:
            return []
        # Cheap path: the gap fits in the in-memory tail.
        tail_start = self._tail[0][0] if self._tail else self._seq + 1
        if since + 1 >= tail_start:
            return [(s, p) for (s, p) in self._tail if s > since]
        # Disk path: subscriber is behind the tail. Read missing range
        # from JSONL up to (but not including) the tail's start, then
        # append the in-memory tail for the rest. The on-disk file
        # has every event so reading the whole file and filtering by
        # seq is fine — JSONL scans are linear.
        return rehydrate(self.run_id, since=since, until=tail_start - 1) \
               + [(s, p) for (s, p) in self._tail if s > since]

    def close(self) -> None:
        """Mark the sink closed. Future pushes are no-ops; subscribers
        get sentinel `None` so their generators can exit cleanly. The
        JSONL handle is flushed + closed (subsequent reattach reads
        from disk if the in-memory sink has been evicted)."""
        if self._closed:
            return
        self._closed = True
        self._closed_at = time.time()
        if self._jsonl_fh is not None:
            try:
                self._jsonl_fh.flush()
                self._jsonl_fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._jsonl_fh = None
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


def live_run_ids() -> set[str]:
    """run_ids whose background turn task is still running in THIS process.
    Used by the Turn reaper (checkpoint.reap_stale_turns) so it never fails a
    turn that is alive right now — only ones whose owning process is gone
    (#14). A sink lingers post-close for replay; liveness is keyed strictly on
    the asyncio task, not sink presence."""
    return {rid for rid, s in _REGISTRY.items()
            if getattr(s, "_task", None) is not None and not s._task.done()}


def live_thread_ids() -> set[str]:
    """thread_ids that own a live turn task in this process — the message-log
    repair must skip these so it can't synthesize an 'interrupted' fill for a
    tool that is still legitimately running."""
    return {s.thread_id for s in _REGISTRY.values()
            if s.thread_id is not None
            and getattr(s, "_task", None) is not None and not s._task.done()}


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


# ---- Disk-replay (C-2) ------------------------------------------------

def rehydrate(run_id: str, *, since: int = 0,
              until: Optional[int] = None) -> list[tuple[int, dict]]:
    """Read events from disk for a Turn that's no longer in-memory (or
    for backfilling the gap between `since` and the in-memory tail's
    start). Returns events with `since < seq <= until` (until=None means
    no upper bound). Empty list if the JSONL doesn't exist.

    Used by both `replay_since` (for sinks whose tail has rolled past
    the subscriber's `since`) and the SSE endpoint (for reattach to a
    closed-and-evicted Turn after backend restart)."""
    path = _jsonl_path(run_id)
    if not path.exists():
        return []
    out: list[tuple[int, dict]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001 — tolerate a partial last line
                    continue
                seq = rec.get("seq", 0)
                if seq <= since:
                    continue
                if until is not None and seq > until:
                    break
                out.append((seq, rec.get("payload") or {}))
    except Exception:  # noqa: BLE001
        return out
    return out


def disk_last_seq(run_id: str) -> int:
    """Highest seq in the JSONL on disk, or 0 if no file / empty.
    Used by the SSE endpoint for the `last_seq` exposed via
    /api/threads/{tid}/active-turn lookups for closed-but-historic
    Turns."""
    path = _jsonl_path(run_id)
    if not path.exists():
        return 0
    last = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    seq = json.loads(line).get("seq", 0)
                    if seq > last:
                        last = seq
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        pass
    return last


# ---- TTL sweeper (C-2) ------------------------------------------------

def sweep_once(*, now: Optional[float] = None) -> dict:
    """Single-pass cleanup of stale state. Returns counts for telemetry.

    Two policies:
      - Evict CLOSED sinks from the registry that have been closed
        longer than CLOSED_SINK_TTL_S (default 1h). The on-disk JSONL
        survives; subsequent reattach reads from disk via rehydrate().
      - Delete JSONL files in TURN_EVENTS_DIR older than
        TURN_EVENTS_TTL_S (default 7d).

    Idempotent + safe to run on a timer or once at startup."""
    if now is None:
        now = time.time()
    sinks_evicted = 0
    files_deleted = 0

    # Evict stale closed sinks.
    stale: list[str] = []
    for rid, s in list(_REGISTRY.items()):
        if s._closed and s._closed_at is not None \
                and (now - s._closed_at) > CLOSED_SINK_TTL_S:
            stale.append(rid)
    for rid in stale:
        _REGISTRY.pop(rid, None)
        sinks_evicted += 1

    # Sweep old JSONL files.
    try:
        dir_ = _turn_events_dir()
        for p in dir_.glob("*.jsonl"):
            try:
                age = now - p.stat().st_mtime
            except OSError:
                continue
            if age > TURN_EVENTS_TTL_S:
                try:
                    p.unlink()
                    files_deleted += 1
                except OSError:
                    pass
    except Exception:  # noqa: BLE001 — sweep failure is non-fatal
        pass

    return {"sinks_evicted": sinks_evicted,
            "files_deleted": files_deleted,
            "registry_size": len(_REGISTRY)}


async def sweep_forever(*, interval_s: int = 3600) -> None:
    """Long-running background task — sweep on a timer. Default 1h
    interval is plenty (TTLs are 1h and 7d). Called from main.py's
    startup hook."""
    while True:
        try:
            sweep_once()
        except Exception:  # noqa: BLE001 — never let a sweep crash the loop
            pass
        await asyncio.sleep(interval_s)


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
