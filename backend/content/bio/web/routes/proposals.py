"""Proposal endpoints (/api/proposals/*).

Proposals are AI-generated next-step nudges produced by the
content/bio/proposals/ detector system. Users can accept (which fires
the proposed action), dismiss, or undo a recent decision.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.web.deps import require_project


router = APIRouter()


@router.post("/api/proposals/{pid}/accept")
def proposal_accept(pid: int, _pid: str = Depends(require_project)):
    from content.bio.proposals.scheduler import accept_proposal
    try:
        return accept_proposal(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/proposals/{pid}/dismiss")
def proposal_dismiss(pid: int, _pid: str = Depends(require_project)):
    from content.bio.proposals.scheduler import dismiss_proposal
    return dismiss_proposal(pid)


@router.post("/api/proposals/{pid}/undo")
def proposal_undo(pid: int, _pid: str = Depends(require_project)):
    from content.bio.proposals.scheduler import undo_proposal
    try:
        return undo_proposal(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))
