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
from fastapi.responses import FileResponse, Response
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
    _run_or_404(rid)
    # nested-path patch: writing the whole `run` object read-modify-wrote the
    # SAME key the manifest writer holds — a concurrent refresh could silently
    # revert this cancellation (recheck-confirmed). Set only the two fields
    # this route owns, atomically.
    from core.graph.entities import patch_metadata
    return patch_metadata(rid, {"run.status": "cancelled",
                                "run.finished_at": _now()})


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
    """Serve a single file from a Run, resolved across tiers (weft retained tree →
    live sandbox; misc/output_durability.md §6.2) so it keeps working past the
    sandbox sweep. `rel` is relative to the run dir; traversal outside is rejected
    on every base. Images/text render inline; `download=1` forces an attachment."""
    _run_or_404(rid)
    from content.bio.lifecycle.runs import resolve_run_file, read_run_file, run_output_site
    name = Path(rel).name
    media = mimetypes.guess_type(name)[0] or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'} if download else {}
    # Tier 1: a local file (retained tree / scratch), OR a remote output whose bytes
    # were fetched into the local cache under the transparent gate → stream from disk.
    target = resolve_run_file(rid, rel)
    if target:
        return FileResponse(target, media_type=media, headers=headers)
    # Tier 2 (B1b): an IN-SANDBOX file (live/dead kernel jobdir, not local) → capped weft
    # preview read. A truncated result means it's past the preview channel — Keep it.
    data, truncated, total = read_run_file(rid, rel)
    if data is not None and not truncated:
        return Response(content=data, media_type=media, headers=headers)
    # Tier-1 declined and the preview is truncated → the file is too big to bring
    # home transparently. Name the site so the message is "on <site>, bring it home
    # to view" instead of an opaque size error (site lookup is best-effort).
    site = None
    try:
        site = run_output_site(rid, rel)
    except Exception:  # noqa: BLE001 — an honest message must never 500 the route
        site = None
    if truncated:
        where = (f" It lives on {site} — bring it home to view it (Keep it, then "
                 f"download)." if site else " Keep it to retain, then download.")
        raise HTTPException(413, f"{rel!r} is {total} bytes — too large to preview.{where}")
    raise HTTPException(404, f"no file {rel!r} in the run (retained or sandbox)")


@router.get("/api/runs/{rid}/archive")
def run_archive(rid: str):
    """ZIP of the Run's locally-servable output files — the run-level "Local
    copy all" (§8e.3). Files whose bytes aren't available from this machine
    (remote in-place keeps, discarded files) are LISTED in a manifest inside
    the zip rather than silently omitted — the archive never lies about
    completeness."""
    _run_or_404(rid)
    import io
    import zipfile
    from content.bio.lifecycle.runs import run_durable_view, resolve_run_file, read_run_file
    view = run_durable_view(rid)
    if not view["files"]:
        raise HTTPException(404, "run has no recorded output files")
    # the zip is assembled IN MEMORY — refuse past the fetch guardrail rather
    # than OOM the controller (the sibling read routes are capped; this
    # aggregate route wasn't — limits-parity review)
    from core.data.datasets import FETCH_GUARDRAIL_BYTES
    total = sum(f.get("bytes") or 0 for f in view["files"]
                if f.get("state") != "cleared")
    if total > FETCH_GUARDRAIL_BYTES:
        raise HTTPException(413, f"outputs total {total / 1e9:.1f} GB — too "
                                 f"large for a single archive; download files "
                                 f"selectively instead")
    buf = io.BytesIO()
    skipped: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in view["files"]:
            rel = f["rel"]
            if f.get("state") == "cleared":
                skipped.append(f"{rel} — discarded (swept by housekeeping)")
                continue
            p = resolve_run_file(rid, rel)
            if p:
                zf.write(p, arcname=rel)
                continue
            data, truncated, _total = read_run_file(rid, rel)
            if data is not None and not truncated:
                zf.writestr(rel, data)
            else:
                where = f" (on {f['site']})" if f.get("site") else ""
                skipped.append(f"{rel} — not available from this machine{where}")
        if skipped:
            zf.writestr("SKIPPED-FILES.txt",
                        "Not included in this archive:\n" + "\n".join(skipped) + "\n")
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{rid}-outputs.zip"'})


@router.get("/api/runs/{rid}/durable")
def run_durable(rid: str, flat: int = 0):
    """The Run's durability view — per-file state (retained / saving / in-store / at-risk /
    in-sandbox / cleared) merged from weft's retained tree + inventory + the live sandbox. Returns a
    TreeNode-compatible tree (root → folders → file nodes with `state`/`badge`) so the
    Files panel renders it directly, plus a `summary`. `?flat=1` returns the flat
    {files, summary} model instead. Backs the sweep-surviving Files listing (§6.2)."""
    _run_or_404(rid)
    from content.bio.lifecycle.runs import run_durable_view, run_durable_tree
    return run_durable_view(rid) if flat else run_durable_tree(rid)


class _KeepBody(BaseModel):
    rel: str


@router.post("/api/runs/{rid}/keep")
def run_keep(rid: str, body: _KeepBody, _pid: str = Depends(require_project)):
    """User late-pin (output_durability.md §6.2): durably retain one of the Run's files on
    demand. Recorded as a level-2 keep decision (`metadata.keep_decision.include`) and
    applied through the CUMULATIVE retain (P1) — a bare retain(include=[rel]) would
    REPLACE the Run's stored selection (weft keeps one row per target) and silently drop
    every earlier keep at settlement. Returns the merged decision + durable summary."""
    run = _run_or_404(rid)
    rel = (body.rel or "").strip()
    if not rel:
        raise HTTPException(400, "rel is required")
    targets = list((run.get("metadata") or {}).get("weft_targets") or [])
    if not targets:
        raise HTTPException(400, "run has no weft target to retain from")
    from content.bio.lifecycle.runs import set_keep_decision
    out = set_keep_decision(rid, keep=[rel])
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return {"ok": True, "rel": rel, "decision": out.get("decision"),
            "summary": out.get("summary")}


@router.post("/api/runs/{rid}/bring-back")
def run_bring_back(rid: str, force: bool = False, _pid: str = Depends(require_project)):
    """§8e.4: ship this Run's kept files to the workspace (managed local copy).
    Location axis only — keeps stay kept where they live. `force=true` waives
    the size guardrail (never a silent multi-GB transfer otherwise)."""
    _run_or_404(rid)
    from content.bio.lifecycle.runs import bring_back_run
    out = bring_back_run(rid, force=force)
    if out.get("error"):
        raise HTTPException(400, out["error"])
    return out


@router.get("/api/runs/{run_id}/artifacts")
def list_run_artifacts(run_id: str):
    """All artifacts produced by every exec attributed to this Run.
    Ordered by the exec's started_at — chat-history order is preserved."""
    from core.exec.artifacts import artifacts_for_run
    return {"artifacts": artifacts_for_run(run_id)}
