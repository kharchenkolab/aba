"""Miscellaneous bio endpoints that don't fit cleanly with one entity.

Covers:
  - /api/messages/pin                pin a chat message → Note → Result
  - /api/results/external            upload an external Result file
  - /api/results/{rid}/upload-evidence   append a file to an existing
                                          Result
  - /api/sample-project              one-click sample dataset
  - /api/home-summary                Home dashboard counts / activity
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.web.deps import require_project
from core.data.paths import unique_path as _unique_path
from core.graph.entities import create_entity, get_entity, update_entity
from core.graph._schema import WORKSPACE_ID
from core.graph.audit import list_advisor_notes, list_context_suggestions

from ._helpers import _resolve_thread
from .results import _result_or_404


router = APIRouter()


# --- /api/messages/pin -------------------------------------------------


class PinMessageRequest(BaseModel):
    key: str                       # stable content hash from the client
    text: str = ""
    title: str = ""
    image_urls: list[str] = []
    thread_id: str = "default"


@router.post("/api/messages/pin")
def pin_message(req: PinMessageRequest, _pid: str = Depends(require_project)):
    """Pin a chat message: create a Note from the text + image_urls and
    wrap it in a Result. Toggles by content key — re-pinning the same
    message archives its Note."""
    from content.bio.graph.search import find_kept_note
    from content.bio.lifecycle.promote import pin_evidence
    existing = find_kept_note(req.key)
    if existing:
        update_entity(existing, status="archived")
        return {"pinned": False}
    tid = _resolve_thread(req.thread_id)
    title = (req.title or req.text).strip().split("\n")[0][:70] or "Kept note"
    out = pin_evidence(
        thread_id=tid, target_result_id=None,
        evidence_kind="note",
        evidence_payload={
            "title": title,
            "metadata": {"source_key": req.key, "text": req.text,
                         "image_urls": req.image_urls},
        },
        interpretation=req.text[:500] or None,
        origin="internal",
    )
    return {"pinned": True, "id": out["evidence_id"], "result_id": out["result_id"]}


# --- Result uploads (file-form endpoints) ----------------------------


@router.post("/api/results/external")
async def upload_external_result(
    file: UploadFile = File(...),
    thread_id: str = Form("default"),
    interpretation: str = Form(""), _pid: str = Depends(require_project)):
    """Bring in an external result (a gel, a wet-lab readout, a figure
    from another tool) as a first-class Result wrapping the upload."""
    from content.bio.lifecycle.promote import pin_evidence
    from content.bio.proposals.scheduler import evaluate_thread
    from core.config import current_project_id, project_artifacts_dir
    if not file.filename:
        raise HTTPException(400, "filename missing")
    pid = current_project_id()
    dest = _unique_path(project_artifacts_dir(pid) / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = _resolve_thread(thread_id)
    out = pin_evidence(
        thread_id=tid, target_result_id=None,
        evidence_kind="figure",
        evidence_payload={
            "title": Path(file.filename).stem,
            "artifact_path": f"/artifacts/{pid}/{dest.name}",
            "metadata": {"original_name": file.filename},
        },
        interpretation=(interpretation or None),
        origin="external",
    )
    evaluate_thread(tid, "data_upload")
    return get_entity(out["result_id"])


@router.post("/api/results/{rid}/upload-evidence")
async def result_upload_evidence(
    rid: str,
    file: UploadFile = File(...),
    caption: str = Form(""), _pid: str = Depends(require_project)):
    """Result-page Add-evidence: upload a file and append it as a NEW
    member of this existing Result. Interpretation is NOT regenerated."""
    from content.bio.lifecycle.promote import pin_evidence
    from core.config import current_project_id, project_artifacts_dir
    r = _result_or_404(rid)
    if not file.filename:
        raise HTTPException(400, "filename missing")
    pid = current_project_id()
    dest = _unique_path(project_artifacts_dir(pid) / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = (r.get("metadata") or {}).get("thread_id") or ""
    pin_evidence(
        thread_id=tid, target_result_id=rid,
        evidence_kind="figure",
        evidence_payload={
            "title": Path(file.filename).stem,
            "artifact_path": f"/artifacts/{pid}/{dest.name}",
            "metadata": {"original_name": file.filename},
        },
        caption=caption, origin="external",
    )
    return get_entity(rid)


# --- Project overview + onboarding -----------------------------------


@router.get("/api/home-summary")
def home_summary(project_id: str | None = None):
    """Dashboard data for Home: counts, recent activity, attention.
    `project_id` pins per-request so Home can preview any project."""
    from core.web.deps import _pin_or_412
    from core.graph.entities import list_entities, count_entities  # noqa
    from core.graph.audit import list_events
    from core.graph.jobs import list_jobs
    _pin_or_412(project_id)
    ents = list_entities(exclude_workspace=True, include_archived=False)
    counts: dict[str, int] = {}
    for e in ents:
        if e["status"] in ("superseded",):
            continue
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    jobs = list_jobs(limit=100)
    suggestions = list_context_suggestions(status="pending")
    note_total = 0
    for e in ents:
        note_total += len(list_advisor_notes(e["id"]))
    created = sorted(
        (e for e in ents if e["type"] != "analysis"),
        key=lambda e: e["created_at"],
    )
    first = created[0]["created_at"] if created else None
    last = max((e["updated_at"] for e in ents), default=None)
    ws = get_entity(WORKSPACE_ID)
    return {
        "project_title": ws["title"] if ws else "Workspace",
        "counts": counts,
        "n_datasets": counts.get("dataset", 0),
        "started_at": first,
        "last_touched": last,
        "recent_events": list_events(limit=8),
        "attention": {
            "pending_suggestions": len(suggestions),
            "active_jobs": len([j for j in jobs if j["status"] in ("queued", "running")]),
            "failed_jobs": len([j for j in jobs if j["status"] == "failed"]),
            "advisor_notes": note_total,
        },
    }


@router.post("/api/sample-project")
def sample_project(_pid: str = Depends(require_project)):
    """One-click sample: register the bundled cells.csv as a dataset."""
    from core.config import current_project_id, project_data_dir
    src = Path(__file__).resolve().parents[4] / "data" / "cells.csv"
    if not src.exists():
        raise HTTPException(500, "sample data missing")
    dest = _unique_path(project_data_dir(current_project_id()) / "sample_cells.csv")
    shutil.copyfile(src, dest)
    from core.graph.derivation import imported, human_actor
    eid = create_entity(
        entity_type="dataset", title=dest.name, artifact_path=str(dest),
        derivation=imported("sample:cells.csv"), actor=human_actor(),   # Phase 2B
        metadata={"size_bytes": dest.stat().st_size, "sample": True},
    )
    return get_entity(eid)
