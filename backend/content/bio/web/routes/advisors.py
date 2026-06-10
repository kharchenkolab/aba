"""Advisor + context-suggestion endpoints.

Covers:
  - /api/advisor-notes/*          per-note status changes (mark
                                  tried/dismissed)
  - /api/entities/{id}/advisor-notes  list notes for an entity
  - /api/entities/{id}/advise     fire on-focus advisor (Explorer
                                  for datasets, Stylist for narratives)
  - /api/context-suggestions/*    adaptive per-type policy suggestions
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.web.deps import require_project
from core.graph.entities import get_entity
from core.graph.audit import (
    list_advisor_notes, set_advisor_note_status,
    list_context_suggestions, update_context_suggestion_status,
    reject_all_pending_suggestions,
)
from content.bio.advisors.runner import explorer_suggest, stylist_review


router = APIRouter()


@router.get("/api/entities/{entity_id}/advisor-notes")
def entities_advisor_notes(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return list_advisor_notes(entity_id)


class AdvisorNoteStatusRequest(BaseModel):
    status: str = "dismissed"


@router.post("/api/advisor-notes/{note_id}/status")
def advisor_note_status(note_id: int, req: AdvisorNoteStatusRequest, _pid: str = Depends(require_project)):
    """Mark a note tried/dismissed so it no longer surfaces as a fresh idea."""
    if not set_advisor_note_status(note_id, req.status):
        raise HTTPException(404, f"Note {note_id} not found")
    return {"ok": True}


@router.post("/api/entities/{entity_id}/advise")
async def entities_advise(entity_id: str, _pid: str = Depends(require_project)):
    """Fire the appropriate on-focus advisor for an entity (Explorer for
    datasets, Stylist for narratives). Idempotent — advisors that have
    already spoken about the entity won't re-fire. Non-blocking."""
    import asyncio
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    loop = asyncio.get_event_loop()
    if e["type"] == "dataset":
        loop.run_in_executor(None, explorer_suggest, entity_id)
    elif e["type"] == "narrative":
        loop.run_in_executor(None, stylist_review, entity_id)
    return {"ok": True}


# --- Adaptive context (suggestions for per-type policy text) ---------


@router.get("/api/context-suggestions")
def context_suggestions(status: str = "pending", max_age_days: int = 14):
    """List context-policy suggestions awaiting review (or any status).

    `max_age_days` defaults to 14 — older pending items auto-stale out of
    the badge / list. Pass 0 (or any non-positive) to include every age."""
    return list_context_suggestions(
        status=status,
        max_age_days=max_age_days if max_age_days > 0 else None,
    )


class SuggestionAction(BaseModel):
    action: str  # 'approve' | 'reject'


@router.post("/api/context-suggestions/{sid}/action")
def context_suggestion_action(sid: int, req: SuggestionAction, _pid: str = Depends(require_project)):
    """Apply a reviewer action:
      approve → status='promoted' + append to the per-type policy file
      reject  → status='rejected'"""
    from content.bio.lifecycle.adaptive import append_to_policy
    if req.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")
    pending = [s for s in list_context_suggestions(status=None, max_age_days=None)
               if s["id"] == sid]
    if not pending:
        raise HTTPException(404, f"suggestion {sid} not found")
    suggestion = pending[0]
    if req.action == "approve":
        append_to_policy(suggestion["entity_type"], suggestion["suggestion"])
        update_context_suggestion_status(sid, "promoted")
    else:
        update_context_suggestion_status(sid, "rejected")
    return {"ok": True}


@router.post("/api/context-suggestions/reject-all")
def context_suggestion_reject_all(_pid: str = Depends(require_project)):
    """Bulk-reject every pending suggestion (any age). Returns count rejected."""
    return {"rejected": reject_all_pending_suggestions()}
