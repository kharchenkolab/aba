"""Background task wrapper that runs the agent loop independently of
any HTTP request — C-1 of the durable-turns redesign
(misc/durable_turns_plan.md §C-1).

Before C-1, the agent loop body in `guide.py:stream_response` was an
async generator that yielded SSE chunks directly. Its lifetime was tied
to the HTTP request — a client disconnect cancelled the generator
mid-tool-await, leaving the Turn stranded.

This module decouples the two:
  * `start_turn(...)` allocates a `TurnSink`, spawns the agent loop as
    a long-lived asyncio task, and returns the sink. The task drains
    `stream_response`'s yielded dicts into the sink. The HTTP request
    is just a subscriber on the sink; client disconnect unsubscribes
    but the task keeps running.
  * A reconnecting client opens a new subscription via
    `GET /api/turns/{rid}/stream?since=<seq>` — same sink, automatic
    replay from the in-memory tail.
"""
from __future__ import annotations
import asyncio
from typing import AsyncGenerator, Optional

from core.runtime import turn_sink as _ts
from core.runtime.turn import gen_run_id


def new_run_id() -> str:
    """Public alias for gen_run_id — callers should allocate the run_id
    before `start_turn` so the sink can be created upfront and the
    initial `manifest` event has the same id the caller already knows."""
    return gen_run_id()


async def _drain(gen: AsyncGenerator[dict, None], sink: "_ts.TurnSink",
                 pid: Optional[str] = None) -> None:
    """Background body: consume `gen`'s yielded dicts and push them onto
    `sink`. Closes the sink in a finally block so subscribers see the
    `None` sentinel even on early exit / exception.

    `pid` is the project this turn belongs to, captured by start_turn while
    the originating request's pin was still in effect. We re-bind it HERE,
    inside the task, via projects.bind() so every _conn() the agent loop
    makes resolves to this project's DB — even though the task outlives the
    request and concurrent requests for other projects keep repointing the
    process-global DB (the 2026-06 cross-project corruption race)."""
    from core import projects as _projects
    try:
        with _projects.bind(pid):
            async for obj in gen:
                if not isinstance(obj, dict):
                    continue    # defensive — emit() should only yield dicts
                sink.push(obj)
    except asyncio.CancelledError:
        # The task itself was cancelled (rare — we don't cancel from the
        # request path anymore; only an explicit Stop fires the cancel
        # token, and that's checked inside the loop, not via task cancel).
        # Best-effort note to subscribers before the sentinel.
        try:
            sink.push({"type": "error", "text": "turn cancelled",
                       "detail": "executor task cancelled"})
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as e:  # noqa: BLE001
        # The generator's own try/except should have already emitted a
        # `done` (or `error`); this catches anything that slipped past.
        try:
            sink.push({"type": "error", "text": str(e),
                       "detail": f"{type(e).__name__}: {e}"})
            sink.push({"type": "done"})
        except Exception:  # noqa: BLE001
            pass
    finally:
        _ts.close(sink.run_id)


def start_turn(
    *,
    run_id: str,
    thread_id: Optional[str],
    started_at: str,
    body_gen: AsyncGenerator[dict, None],
) -> "_ts.TurnSink":
    """Allocate a sink for `run_id` and spawn the agent loop as a
    background task. `body_gen` is the agent-loop async generator
    (today: `stream_response(...)` yielding dicts).

    Returns the sink so the caller can immediately subscribe with
    `stream_from_sink(sink, since=0)` for the initial SSE response.

    Idempotent on `run_id`: a second call returns the existing sink
    (and skips re-spawning) — the caller should ensure run_ids are
    unique."""
    existing = _ts.get(run_id)
    if existing is not None:
        # Pre-existing — don't spawn a duplicate task. Close the unused
        # generator first so it doesn't leak.
        try:
            asyncio.ensure_future(body_gen.aclose())
        except Exception:  # noqa: BLE001
            pass
        return existing

    # Capture the project NOW, synchronously, while the originating request's
    # per-request pin is still the active global. The detached task below will
    # re-bind it to its own context so concurrent requests can't swap it.
    from core import projects as _projects
    pid = _projects.current()

    sink = _ts.create(run_id, thread_id, started_at)
    task = asyncio.create_task(_drain(body_gen, sink, pid), name=f"turn:{run_id}")
    # Attach the task so we can find / inspect / (rarely) cancel it.
    # Sink stays alive after the task completes — see core/runtime/turn_sink.py.
    sink._task = task  # type: ignore[attr-defined]
    return sink
