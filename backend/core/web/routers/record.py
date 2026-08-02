"""Record routes — the World projection (RECORD_DESIGN.md §13.3 phase 1)
plus the face's smallest write: pin (§6's universal curation gesture).
Domain-neutral (core.record.*); the face is a renderer over this one
endpoint, so alternative UIs stay projections of the same graph."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.record.world import assemble_world, record_roles
from core.web.deps import require_project

router = APIRouter()


@router.get("/api/record/world")
def record_world(pid: str = Depends(require_project),
                 sediment_limit: int = 200,
                 since: str | None = None):
    world = assemble_world(sediment_limit=sediment_limit, since=since)
    world["project_id"] = pid
    return world


class PinRequest(BaseModel):
    thread_id: str
    text: str            # the excerpt being pinned — "this must not evaporate"
    ts: str | None = None  # the message's timestamp (turn-grade provenance)


@router.post("/api/record/pin")
def record_pin(req: PinRequest, _pid: str = Depends(require_project)):
    """Pin a transcript excerpt as a NOTE on its line — filed directly (the
    user's own noticing needs no ratification, §6); the type comes from the
    pack's registered note role, so core stays domain-neutral."""
    ntype = record_roles().get("note")
    if not ntype:
        raise HTTPException(412, "no note role registered for this content pack")
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(422, "nothing to pin")
    from core.graph.entities import create_entity
    eid = create_entity(
        entity_type=ntype,
        title=text[:117] + ("…" if len(text) > 117 else ""),
        metadata={"thread_id": req.thread_id, "text": text[:4000],
                  "source": "record_pin",
                  **({"pinned_at_ts": req.ts} if req.ts else {})})
    return {"id": eid}
