"""Record routes — the read-only World projection (RECORD_DESIGN.md §13.3
phase 1). Domain-neutral (core.record.*); the face is a renderer over this
one endpoint, so alternative UIs stay projections of the same graph."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from core.record.world import assemble_world
from core.web.deps import require_project

router = APIRouter()


@router.get("/api/record/world")
def record_world(pid: str = Depends(require_project),
                 sediment_limit: int = 200):
    world = assemble_world(sediment_limit=sediment_limit)
    world["project_id"] = pid
    return world
