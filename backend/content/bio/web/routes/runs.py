"""Run endpoints (/api/runs/*).

A Run is an `analysis`-typed entity wrapping a single notebook execution
+ its outputs. See entity_model_v3 §4.

NOTE: revision flow, exec_record/artifact addressing, and the
related /api/entities/{id}/{reproduce,make_revision,delete-revision,
revisions} endpoints live in the sibling revisions.py module — they
share the Run-centric mental model but the file was over the 300 LOC
limit if kept together.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.web.deps import require_project
from core.graph.entities import create_entity, get_entity, update_entity
from core.graph.edges import add_edge

from ._helpers import _now


router = APIRouter()


def _run_or_404(rid: str) -> dict:
    e = get_entity(rid)
    if not e or e["type"] != "analysis":
        raise HTTPException(404, f"Run {rid} not found")
    return e


@router.post("/api/runs/{rid}/refresh-manifest")
def runs_refresh_manifest(rid: str, _pid: str = Depends(require_project)):
    """Re-scan a Run's output dir and rebuild its manifest."""
    from content.bio.lifecycle.runs import refresh_output_manifest
    e = get_entity(rid)
    if not e or e.get("type") != "analysis":
        raise HTTPException(404, f"run {rid} not found")
    refresh_output_manifest(rid)
    return {"ok": True}


@router.post("/api/runs/{rid}/cancel")
def run_cancel(rid: str, _pid: str = Depends(require_project)):
    e = _run_or_404(rid)
    meta = dict(e.get("metadata") or {})
    run = dict(meta.get("run") or {})
    run["status"] = "cancelled"
    run["finished_at"] = _now()
    meta["run"] = run
    return update_entity(rid, metadata=meta)


class PinOutputRequest(BaseModel):
    kind: str = "figure"
    label: str = ""
    thumb: str | None = None
    href: str | None = None
    size: str | None = None
    interpretation: str = ""


@router.post("/api/runs/{rid}/pin-output")
def run_pin_output(rid: str, req: PinOutputRequest, _pid: str = Depends(require_project)):
    """Pin one of a run's outputs as a Result wrapping the evidence
    (figure/table). Plots/tables we can render are kept with their
    thumbnail; everything else is a reference (origin=external + href)."""
    from content.bio.lifecycle.promote import pin_evidence
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id") or ""
    etype = "table" if req.kind == "table" else "figure"
    is_img = bool(req.thumb) and req.thumb.lower().rsplit(".", 1)[-1] in (
        "png", "jpg", "jpeg", "svg", "webp", "gif")
    out = pin_evidence(
        thread_id=tid, target_result_id=None,
        evidence_kind=etype,
        evidence_payload={
            "title": req.label or "result",
            "artifact_path": (req.thumb if is_img else None),
            "metadata": {"source_run": rid, "href": req.href, "out_kind": req.kind},
        },
        interpretation=(req.interpretation or None),
        origin="external", parent_run_id=rid,
    )
    return get_entity(out["result_id"])


class RegisterDatasetRequest(BaseModel):
    label: str = ""
    path: str | None = None       # filesystem path / href the bundle lives at
    size: str | None = None
    summary: str = ""


@router.post("/api/runs/{rid}/register-dataset")
def run_register_dataset(rid: str, req: RegisterDatasetRequest, _pid: str = Depends(require_project)):
    """Lift a run's PRIMARY artifact into a first-class Dataset entity —
    by reference: we record where it lives, we do not host a copy."""
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id")
    # By-reference datasets still have an artifact_path — the
    # remote/local path the data lives at. Satisfies dataset.yaml's
    # required field; by_reference + ref_path metadata makes the
    # semantics explicit (we don't host a local copy).
    ref_path = req.path or ""
    from core.graph.derivation import derived_from, human_actor
    eid = create_entity(
        entity_type="dataset", title=req.label or "dataset",
        artifact_path=ref_path or None,
        derivation=derived_from([rid]), actor=human_actor(),   # Phase 2B: produced by the run
        metadata={"thread_id": tid, "origin": "external", "by_reference": True,
                  "ref_path": ref_path, "size_label": req.size,
                  "summary": req.summary, "source_run": rid})
    add_edge(eid, rid, "produced_by")
    return get_entity(eid)


@router.get("/api/runs/{rid}/tree")
def run_tree(rid: str):
    """The Run's subtree from the files tree (readme, code, output/ dir +
    curated figures/tables) — so the Run view can embed FileBrowser."""
    _run_or_404(rid)
    from content.bio.files.tree import build_files_tree

    tree = build_files_tree(include_archived=False)

    def _find(node):
        if node.get("entity_id") == rid and node.get("kind") == "folder":
            return node
        for c in node.get("children") or []:
            hit = _find(c)
            if hit:
                return hit
        return None

    node = _find(tree)
    if node is None:
        # Run exists but isn't placed in the tree yet (e.g. no outputs).
        return {"kind": "root", "name": "", "path": "", "children": []}
    return {**node, "kind": "root"}


@router.get("/api/runs/{rid}/file")
def run_file(rid: str, rel: str, download: int = 0):
    """Serve a single file from a Run's output directory. `rel` is the
    path relative to the run dir; traversal outside is rejected.
    Images/text render inline; `download=1` forces an attachment."""
    run = _run_or_404(rid)
    base = run.get("artifact_path")
    if not base:
        raise HTTPException(404, "run has no output directory")
    base_p = Path(base).resolve()
    target = (base_p / rel).resolve()
    if base_p != target and base_p not in target.parents:
        raise HTTPException(400, "path escapes the run directory")
    if not target.is_file():
        raise HTTPException(404, f"no file {rel!r} in the run output")
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{target.name}"'} if download else {}
    return FileResponse(str(target), media_type=media, headers=headers)


@router.get("/api/runs/{run_id}/artifacts")
def list_run_artifacts(run_id: str):
    """All artifacts produced by every exec attributed to this Run.
    Ordered by the exec's started_at — chat-history order is preserved."""
    from core.exec.artifacts import artifacts_for_run
    return {"artifacts": artifacts_for_run(run_id)}
