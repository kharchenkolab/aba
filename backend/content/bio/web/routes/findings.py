"""Finding + Narrative endpoints (/api/findings/*, /api/narratives).

A Finding is the synthesis: a kept conclusion stitched from one or more
Results. See content/bio/lifecycle/promote.py for the promotion helpers
this module dispatches to.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.web.deps import require_project
from core.graph.entities import create_entity, get_entity
from content.bio.lifecycle.promote import (
    promote_results_to_finding,
    add_result_to_finding, remove_result_from_finding,
)


router = APIRouter()


class PromoteResultsRequest(BaseModel):
    result_ids: list[str]
    text: str
    title: str | None = None


@router.post("/api/findings")
def create_finding(req: PromoteResultsRequest, _pid: str = Depends(require_project)):
    try:
        fid = promote_results_to_finding(req.result_ids, req.text, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(fid)


class NarrativeRequest(BaseModel):
    title: str
    text: str = ""


@router.post("/api/narratives")
def create_narrative(req: NarrativeRequest, _pid: str = Depends(require_project)):
    from core.graph.derivation import manual, human_actor
    eid = create_entity(
        entity_type="narrative",
        title=req.title or "Untitled section",
        derivation=manual(), actor=human_actor(),   # Phase 2B
        metadata={"text": req.text},
    )
    return get_entity(eid)


class FindingResultRequest(BaseModel):
    result_id: str


class DraftFindingRequest(BaseModel):
    text: str = ""                 # concatenated text of selected messages
    title_hint: str = ""           # e.g. the first user message in the selection
    image_urls: list[str] = []     # figure/plot urls in the selection


@router.post("/api/findings/draft")
def draft_finding(req: DraftFindingRequest, _pid: str = Depends(require_project)):
    """Selection-to-finding draft. Heuristic for now (no tokens): title
    from the ask, summary from the discussion, evidence resolved from
    the figures referenced in the selection."""
    from core.graph.entities import list_entities as _le
    text = req.text.strip()
    first = (req.title_hint or text).strip().split("\n")[0]
    title = (first[:80] + ("…" if len(first) > 80 else "")) or "Untitled finding"
    summary = text[:600] + ("…" if len(text) > 600 else "")
    urls = set(req.image_urls or [])
    evidence = []
    if urls:
        for e in _le():
            if e.get("artifact_path") in urls and e["type"] in ("figure", "table"):
                evidence.append({"id": e["id"], "type": e["type"], "title": e["title"]})
    return {"title": title, "summary": summary, "evidence": evidence, "caveats": []}


class CreateFindingRequest(BaseModel):
    title: str
    summary: str = ""
    evidence_ids: list[str] = []
    caveats: list[dict] = []
    status: str = "candidate"


@router.post("/api/findings/from-draft")
def create_finding_endpoint(req: CreateFindingRequest, _pid: str = Depends(require_project)):
    from content.bio.lifecycle.promote import create_finding_from_draft
    fid = create_finding_from_draft(req.title, req.summary, req.evidence_ids,
                                    req.caveats, req.status)
    return get_entity(fid)


class FindingFieldsRequest(BaseModel):
    summary: str | None = None
    caveats: list[dict] | None = None
    status: str | None = None
    title: str | None = None


@router.post("/api/findings/{finding_id}/fields")
def finding_fields(finding_id: str, req: FindingFieldsRequest, _pid: str = Depends(require_project)):
    from content.bio.lifecycle.promote import set_finding_fields
    try:
        return set_finding_fields(finding_id, summary=req.summary,
                                  caveats=req.caveats, status=req.status,
                                  title=req.title)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/findings/{finding_id}/add-result")
def finding_add_result(finding_id: str, req: FindingResultRequest, _pid: str = Depends(require_project)):
    try:
        return add_result_to_finding(finding_id, req.result_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/findings/{finding_id}/remove-result")
def finding_remove_result(finding_id: str, req: FindingResultRequest, _pid: str = Depends(require_project)):
    try:
        return remove_result_from_finding(finding_id, req.result_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
