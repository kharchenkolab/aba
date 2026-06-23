"""Exec-record / artifact addressing + revision-flow endpoints.

Covers:
  - /api/exec_records/{exec_id}/artifacts          list artifacts
  - /api/exec_records/{exec_id}/pin_cell           pin cell entity
  - /api/artifacts/{exec_id}/{kind}/{idx}          three-tuple resolve
  - /api/artifacts/{exec_id}/{kind}/{idx}/pin      pin into Result
  - /api/entities/{id}/revisions                   chain navigation
  - /api/entities/{id}/{make,delete}-revision      revise/delete
  - /api/entities/{id}/reproduce                   re-run for env-drift check

Split from runs.py because the combined module exceeded the 300 LOC
limit. The mental model — "an exec record is a Run's atomic execution
event; revisions are Run-shaped offspring" — is shared with runs.py.

See misc/exec_records_and_versioning.md (Stages 1, 5, 6) for the
artifact-address scheme and revision flow.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.web.deps import require_project
from core.graph.entities import get_entity


router = APIRouter()


# --- Artifact addressing (Option B / Phase 1) ------------------------


@router.get("/api/exec_records/{exec_id}/artifacts")
def list_exec_artifacts(exec_id: str):
    """All artifacts an exec record produced. Filter via
    ?kind=figure|table|cell|file."""
    from core.exec.artifacts import list_artifacts
    out = list_artifacts(exec_id)
    return {"artifacts": out}


@router.get("/api/artifacts/{exec_id}/{kind}/{idx}")
def resolve_artifact_endpoint(exec_id: str, kind: str, idx: int):
    """Resolve a single artifact by its three-tuple address. 404 if the
    exec record is gone, the index is out of range, or the kind doesn't
    match at that index."""
    from core.exec.artifacts import resolve_artifact
    a = resolve_artifact(exec_id, kind, idx)
    if a is None:
        raise HTTPException(404, f"artifact {exec_id}:{kind}:{idx} not found")
    return a


class PinArtifactRequest(BaseModel):
    title: str | None = None
    wrap_in_result: bool = True


@router.post("/api/artifacts/{exec_id}/{kind}/{idx}/pin")
def pin_artifact_endpoint(exec_id: str, kind: str, idx: int,
                           req: PinArtifactRequest, _pid: str = Depends(require_project)):
    """Materialize an artifact as an entity and (by default) wrap it in a
    Result. Idempotent: re-pinning reuses the existing entity. Fires
    auto_interpret on new pins for the AI-generated title + caption."""
    from content.bio.lifecycle.artifacts import pin_artifact
    from content.bio.lifecycle.promote import auto_interpret
    try:
        out = pin_artifact(exec_id, kind, idx,
                            title=req.title,
                            wrap_in_result=req.wrap_in_result)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Fire auto_interpret only when a NEW Result was created. Pre-PIN-B
    # this was gated on `was_new` (entity-level); that under-fired in the
    # orphan-entity case (entity reused but Result freshly wrapped) and
    # over-fired in the dupe-Result case (which can no longer happen now
    # that pin_evidence dedupes). was_new_result is the correct signal.
    if req.wrap_in_result and out.get("result_id") and out.get("was_new_result"):
        import threading
        threading.Thread(target=auto_interpret, args=(out["result_id"],),
                         daemon=True).start()
    entity = get_entity(out["entity_id"])
    return {**out, "entity": entity}


class PinCellRequest(BaseModel):
    title: str | None = None
    wrap_in_result: bool = True


@router.post("/api/exec_records/{exec_id}/pin_cell")
def pin_cell_endpoint(exec_id: str, req: PinCellRequest, _pid: str = Depends(require_project)):
    """Pin the output of `exec_id` as a `cell` entity (Stage 6 of
    misc/exec_records_and_versioning.md). When wrap_in_result=True
    (default), also wraps the cell in a Result."""
    from content.bio.lifecycle.cells import create_cell_from_exec, pin_cell_from_exec
    try:
        if req.wrap_in_result:
            out = pin_cell_from_exec(exec_id, title=req.title)
        else:
            cell_id = create_cell_from_exec(exec_id, title=req.title)
            out = {"cell_id": cell_id, "result_id": None, "member_id": None}
    except ValueError as e:
        raise HTTPException(400, str(e))
    cell = get_entity(out["cell_id"]) if out.get("cell_id") else None
    return {
        "cell": cell,
        "result_id": out.get("result_id"),
        "member_id": out.get("member_id"),
    }


# --- Revision navigation + operations (Stage 5) ----------------------


@router.get("/api/entities/{entity_id}/revisions")
def list_revisions(entity_id: str):
    """Return the revision chain for a figure/table entity, newest first.
    Follows wasRevisionOf edges in both directions so the same chain is
    returned regardless of which revision the caller is looking at."""
    from content.bio.graph.figure_history import figure_history
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    chain = figure_history(entity_id)
    pos = next((i for i, e in enumerate(chain) if e["id"] == entity_id), 0)
    return {
        "chain": chain,
        "position": pos,
        "prev": chain[pos + 1]["id"] if pos + 1 < len(chain) else None,
        "next": chain[pos - 1]["id"] if pos > 0 else None,
    }


class MakeRevisionRequest(BaseModel):
    modified_code: str
    title: str | None = None
    # When True, allows revising from a non-latest revision; any newer
    # entries in the chain get marked status='superseded' so the
    # displayed chain stays linear. Default False = refuse.
    supersede_newer: bool = False


@router.post("/api/entities/{entity_id}/delete-revision")
def delete_revision_endpoint(entity_id: str, _pid: str = Depends(require_project)):
    """Hard-delete a single figure/table revision, preserving chain
    integrity. 400 if `entity_id` is the only active version in its
    chain. See lifecycle/revisions.py:delete_revision."""
    from content.bio.lifecycle.revisions import delete_revision
    try:
        out = delete_revision(entity_id)
    except ValueError as e:
        msg = str(e)
        if "not found" in msg:
            raise HTTPException(404, msg)
        raise HTTPException(400, msg)
    return out


@router.post("/api/entities/{entity_id}/make_revision")
def make_revision_endpoint(entity_id: str, req: MakeRevisionRequest, _pid: str = Depends(require_project)):
    """Run `modified_code` and pin the new artifact as wasRevisionOf
    `entity_id`. If `entity_id` has newer revisions and supersede_newer
    is False, responds 400 with a `newer` list so the UI can confirm."""
    from content.bio.lifecycle.revisions import make_revision
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    try:
        out = make_revision(
            entity_id, req.modified_code,
            title=req.title,
            thread_id=(ent.get("metadata") or {}).get("thread_id"),
            supersede_newer=req.supersede_newer,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    new_ent = get_entity(out["new_entity_id"])
    return {
        "entity": new_ent,
        "exec_id": out.get("exec_id"),
        "wasRevisionOf": out.get("wasRevisionOf"),
        "superseded": out.get("superseded", []),
    }


@router.post("/api/entities/{entity_id}/reproduce")
def reproduce_endpoint(entity_id: str, _pid: str = Depends(require_project)):
    """Re-run the exec that produced `entity_id` and report. Doesn't
    create any new entity — caller may follow up with /make_revision."""
    from content.bio.lifecycle.revisions import reproduce_from_exec
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    try:
        return reproduce_from_exec(
            entity_id,
            thread_id=(ent.get("metadata") or {}).get("thread_id"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
