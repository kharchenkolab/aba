"""Turn-infrastructure read routes — list/get + the C-1 reattach SSE stream.
Extracted from main.py (Item 2A.3). Domain-neutral (core.runtime.checkpoint /
turn_sink). Reasoning-plane entries (chat, resume, tool_result) stay in main.py
because they call guide.stream_response, which core/ must not import (seam rule 4).

`/api/threads/{tid}/active-turn` stays in main too — it calls `_conn()` directly,
which the store-port guard confines to core/graph/ + main.py.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core.web.deps import require_project_context

router = APIRouter()


@router.get("/api/turns")
def turns_list(limit: int = 50):
    """Recent Turn checkpoints (arch3_plan.md Pass E). For diagnostic
    inspection; the resume endpoint lives at /api/turns/{run_id}/resume
    once the full state-machine extraction (Pass F) lands."""
    from core.runtime.checkpoint import list_recent_turns
    return list_recent_turns(limit=limit)


@router.get("/api/turns/{run_id}")
def turn_get(run_id: str):
    """Single-Turn lookup — what state was the loop in, what's pending."""
    from core.runtime.checkpoint import load_turn
    t = load_turn(run_id)
    if t is None:
        raise HTTPException(404, "no such run")
    return t.to_row()


@router.get("/api/turns/{run_id}/stream")
async def turn_stream(run_id: str, since: int = 0, project_id: str | None = None):
    require_project_context(project_id)
    """C-1 reattach: subscribe to an in-flight Turn's event sink and
    stream its events as SSE. Replays any events with seq > since from
    the in-memory tail, then live-streams new ones. Heartbeats every
    ~25s keep the connection alive through idle periods.

    Client disconnect just unsubscribes — the agent loop is untouched
    and the next reconnect with `?since=<lastSeq>` resumes from where
    the client left off.

    Returns 410 Gone if the sink isn't in the registry (process restart
    or evicted by future C-2 sweeper) AND the Turn is already terminal
    in the DB — nothing to subscribe to and nothing to replay.
    Returns 404 if the run_id is unknown."""
    from core.runtime import turn_sink as _ts
    from core.runtime.checkpoint import load_turn
    sink = _ts.get(run_id)
    if sink is None:
        # No live sink — either the Turn never existed, completed
        # before this process started, or was evicted by the TTL
        # sweeper. C-2: try a disk replay from the JSONL file. If we
        # have it on disk, replay everything since `since` and then
        # close (the agent loop is gone — there's no live tail).
        disk = _ts.rehydrate(run_id, since=since)
        if disk:
            async def _disk_replay():
                import json as _json
                for seq, payload in disk:
                    obj = dict(payload)
                    obj["seq"] = seq
                    yield f"data: {_json.dumps(obj, default=str)}\n\n"
                # No terminal `done` injection — if the loop finished
                # cleanly, the original `done` event is in the JSONL.
                # If it didn't (process crash mid-flight), the reaper
                # marked the Turn FAILED and the client renders that
                # via /api/turns/{rid} polling.
            return StreamingResponse(
                _disk_replay(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        # No disk file either — fall through to DB-state check.
        t = load_turn(run_id)
        if t is None:
            raise HTTPException(404, f"no such run: {run_id}")
        if t.state.value in ("done", "failed"):
            # Emit a synthetic single-event stream so the client's
            # handler runs `done` and cleans up cleanly.
            async def _terminal():
                import json as _json
                yield f"data: {_json.dumps({'type': 'done', 'seq': 0})}\n\n"
            return StreamingResponse(
                _terminal(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        raise HTTPException(410, f"run {run_id} sink no longer available")
    return StreamingResponse(
        _ts.stream_from_sink(sink, since=since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
