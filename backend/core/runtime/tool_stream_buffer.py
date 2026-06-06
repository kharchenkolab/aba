"""Per-tool_use_id live-stream buffer for #334 Phase 2 replay-on-reconnect.

Each `tool_chunk` SSE event is ALSO recorded here, keyed by
(run_id, tool_use_id). When the frontend reconnects (SSE resume, tab focus,
fresh tab) and sees a tool_start without a matching tool_result, it GETs
`/api/turns/{run_id}/tool_stream/{tool_use_id}` and rehydrates the live
drawer from the snapshot.

Memory bound:
  - Each buffer is capped at `TOOL_OUTPUT_CAP_CHARS` PER STREAM (default 50K).
    On overflow, we apply the same middle-snip the model sees — so the
    rehydrated drawer matches the eventual tool_result shape.
  - On `mark_done`, the buffer's TTL is set to RETAIN_AFTER_DONE_S (5 min)
    so a tab opened after completion can still rehydrate. Then GC drops it.
  - Running buffers (no mark_done yet) also expire after RUNNING_HARD_TTL_S
    (1 hour) as a backstop against orphans (cancelled turns, server restarts
    that didn't get to mark_done).

Thread-safe: a single module-level RLock protects the dict. Pushes happen
from the guide-loop drain coroutine; reads happen from the FastAPI handler
thread. Both are short critical sections.
"""
from __future__ import annotations
import threading
import time
from typing import Optional

from core.config import TOOL_OUTPUT_CAP_CHARS
from core.exec.output_cap import snip_middle


RETAIN_AFTER_DONE_S = 300.0    # 5 min — slow tab reopens still rehydrate
RUNNING_HARD_TTL_S = 3600.0    # 1 hour — backstop for orphaned running buffers
GC_INTERVAL_S = 60.0           # how often the cleaner runs


class _Buffer:
    __slots__ = ("stdout", "stderr", "bytes_stdout", "bytes_stderr",
                 "elapsed_s", "started_at", "last_chunk_at",
                 "status", "expires_at")

    def __init__(self) -> None:
        self.stdout: str = ""
        self.stderr: str = ""
        self.bytes_stdout: int = 0
        self.bytes_stderr: int = 0
        self.elapsed_s: float = 0.0
        now = time.time()
        self.started_at: float = now
        self.last_chunk_at: float = now
        self.status: str = "running"  # "running" | "done"
        self.expires_at: float = now + RUNNING_HARD_TTL_S

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "stdout": snip_middle(self.stdout, TOOL_OUTPUT_CAP_CHARS),
            "stderr": snip_middle(self.stderr, TOOL_OUTPUT_CAP_CHARS),
            "bytes_stdout": self.bytes_stdout,
            "bytes_stderr": self.bytes_stderr,
            "elapsed_s": self.elapsed_s,
            "started_at": self.started_at,
            "last_chunk_at": self.last_chunk_at,
        }


_lock = threading.RLock()
_buffers: dict[tuple[str, str], _Buffer] = {}
_last_gc = [0.0]


def _maybe_gc(now: float) -> None:
    if now - _last_gc[0] < GC_INTERVAL_S:
        return
    _last_gc[0] = now
    expired = [k for k, b in _buffers.items() if b.expires_at <= now]
    for k in expired:
        _buffers.pop(k, None)


def record_chunk(run_id: str, tool_use_id: str, *,
                 stream: str, text: str,
                 bytes_total: int, elapsed_s: float) -> None:
    """Append a coalesced chunk to the buffer for this (run_id, tool_use_id).
    Bounded growth: per-stream buffer is hard-capped at 2× TOOL_OUTPUT_CAP_CHARS
    of raw text; snip_middle is applied at READ time so the rehydrated drawer
    matches the model's view. (We keep raw 2× so a very late chunk doesn't lose
    head bytes that already got snipped.)"""
    if not run_id or not tool_use_id or not text:
        return
    cap_raw = TOOL_OUTPUT_CAP_CHARS * 2
    with _lock:
        key = (run_id, tool_use_id)
        buf = _buffers.get(key)
        if buf is None:
            buf = _Buffer()
            _buffers[key] = buf
        if stream == "stderr":
            buf.stderr = (buf.stderr + text)[-cap_raw:]
            buf.bytes_stderr = bytes_total
        else:
            buf.stdout = (buf.stdout + text)[-cap_raw:]
            buf.bytes_stdout = bytes_total
        buf.elapsed_s = elapsed_s
        buf.last_chunk_at = time.time()
        _maybe_gc(buf.last_chunk_at)


def ensure(run_id: str, tool_use_id: str) -> None:
    """Create an empty buffer for this (run_id, tool_use_id) if absent.

    Called at tool dispatch start so EVERY tool — including non-streaming
    ones like create_scenario, present_plan, write_memory — has a buffer
    the /api/turns/.../tool_stream/... endpoint can return. Without this
    those tools 404 on the poll and the frontend can't show "in-flight"
    or "completed-with-error" state in the drawer.

    Idempotent — no-op if a buffer already exists (which is the common
    case for run_python/run_r where record_chunk created the buffer
    before the dispatch wrapper called ensure)."""
    if not run_id or not tool_use_id:
        return
    with _lock:
        key = (run_id, tool_use_id)
        if key not in _buffers:
            _buffers[key] = _Buffer()


def record_error(run_id: str, tool_use_id: str, error_text: str) -> None:
    """Push an error message into the buffer's stderr so the UI's live-tail
    drawer surfaces it. For tools that return {"error": "..."} without ever
    emitting stderr (most non-streaming tools — create_scenario,
    present_plan, etc.), this is the only path that gets the error into
    the drawer view."""
    if not run_id or not tool_use_id or not error_text:
        return
    cap_raw = TOOL_OUTPUT_CAP_CHARS * 2
    with _lock:
        key = (run_id, tool_use_id)
        buf = _buffers.get(key)
        if buf is None:
            buf = _Buffer()
            _buffers[key] = buf
        sep = "\n" if buf.stderr else ""
        buf.stderr = (buf.stderr + sep + error_text)[-cap_raw:]
        buf.bytes_stderr = len(buf.stderr.encode("utf-8", errors="replace"))
        buf.last_chunk_at = time.time()


def mark_done(run_id: str, tool_use_id: str) -> None:
    """Flip status → done, set short retention TTL. Idempotent."""
    if not run_id or not tool_use_id:
        return
    with _lock:
        buf = _buffers.get((run_id, tool_use_id))
        if buf is None:
            return
        buf.status = "done"
        buf.expires_at = time.time() + RETAIN_AFTER_DONE_S


def get(run_id: str, tool_use_id: str) -> Optional[dict]:
    if not run_id or not tool_use_id:
        return None
    with _lock:
        buf = _buffers.get((run_id, tool_use_id))
        if buf is None:
            return None
        if buf.expires_at <= time.time():
            _buffers.pop((run_id, tool_use_id), None)
            return None
        return buf.snapshot()


def _clear_for_tests() -> None:
    """Reset all state — for test isolation only."""
    with _lock:
        _buffers.clear()
        _last_gc[0] = 0.0
