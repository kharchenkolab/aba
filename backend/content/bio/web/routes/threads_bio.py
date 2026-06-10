"""Bio-flavored thread endpoints.

Note: thread CRUD (/api/threads, /api/threads/{tid}, /api/threads/{tid}/messages)
lives in backend/main.py — that's the platform surface. The two endpoints
here are bio-specific behavior anchored to a thread (proposal evaluation
and cold-start orientation by the Guide).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.web.deps import require_project

from ._helpers import _resolve_thread


router = APIRouter()


class EvaluateRequest(BaseModel):
    trigger: str = "post_turn"


@router.get("/api/threads/{tid}/proposals")
def thread_proposals(tid: str, status: str = "pending"):
    from core.graph.proposals_store import list_proposals
    rtid = _resolve_thread(tid)
    return list_proposals(thread_id=rtid, status=(status or None))


@router.post("/api/threads/{tid}/evaluate")
def thread_evaluate(tid: str, req: EvaluateRequest, _pid: str = Depends(require_project)):
    """Run proposal detectors for a thread on demand (used by the
    thread-open event trigger). Post-turn evaluation fires from guide.py."""
    from content.bio.proposals.scheduler import evaluate_thread
    from core.graph.proposals_store import list_proposals
    rtid = _resolve_thread(tid)
    evaluate_thread(rtid, req.trigger)
    return list_proposals(thread_id=rtid, status="pending")


@router.post("/api/threads/{tid}/orient")
def thread_orient(tid: str, _pid: str = Depends(require_project)):
    """Cold-start orientation: the Guide summarizes the project's data +
    suggests next steps. Idempotent — no-ops once the thread has a
    conversation or has already been oriented."""
    from content.bio.lifecycle.orientation import orient_thread
    rtid = _resolve_thread(tid)
    result = orient_thread(rtid)
    return {"oriented": bool(result), "result": result}
