"""Viewer routes — the file-viewer registry + per-node viewer lookup + external-
viewer launch/status/download. Moved out of main.py (Item 2A.4/#1). These are platform
viewer-infra (`core.viewers.*`) that CONSULT bio (files tree + viewer/launcher
registrations via `import content.bio`), so they live in the bio web layer where the
seam permits content imports. `/viewer-launch` (the HTML progress page) + the pagoda3
store/proxy stay in main — they're content-free.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core.graph.entities import get_entity
from core.web.deps import require_project

router = APIRouter()


@router.get("/api/viewers/registry")
def viewers_registry():
    """Full viewer registry — fetched once by the frontend, then used for
    client-side dispatch so each file click doesn't pay a round-trip to
    pick a viewer. The matching metadata (entity_types, extensions,
    mime_patterns, applies_any, max_size_kb) is included alongside the
    wire info so the client can mirror the backend's match logic."""
    import content.bio  # noqa: F401 — ensure viewer registrations
    from core.viewers.registry import list_viewers, to_wire
    out = []
    for v in list_viewers():
        d = to_wire(v)
        d['extensions']     = list(v.extensions)
        d['mime_patterns']  = list(v.mime_patterns)
        d['entity_types']   = list(v.entity_types)
        d['applies_any']    = v.applies_any
        d['max_size_kb']    = v.max_size_kb
        out.append(d)
    return out


def _resolve_files_node(entity_id: str | None, path: str | None) -> dict:
    """Resolve a files-tree node from either an entity_id (entity-backed
    file) or a path (any node in the tree). Shared by the viewer lookup
    and launch endpoints. Raises HTTPException on missing/underspecified."""
    if entity_id:
        e = get_entity(entity_id)
        if not e:
            raise HTTPException(404, f"no entity {entity_id}")
        return {
            "entity_id": e["id"],
            "entity_type": e["type"],
            # Match on the FILENAME (basename of artifact_path), not the entity
            # title — viewers_for keys off `name or artifact_path`, and external
            # viewers (pagoda3: .h5ad/.lstar.zarr) match by extension, which a
            # title like "GSM… processed AnnData" lacks. Mirrors the get_viewer_url
            # tool; without this, the launch link 404s ("no external viewer applies").
            "name": Path(e.get("artifact_path") or "").name or e.get("title") or "",
            "artifact_path": e.get("artifact_path"),
            "size": None,
        }
    if path:
        # Tolerant resolve: exact tree path, else a basename / path-suffix match
        # (callers — incl. the agent via open_viewer — rarely know the full path).
        from content.bio.files.tree import build_files_tree, find_file_node, list_file_matches
        tree = build_files_tree(include_archived=False)
        n = find_file_node(tree, path)
        if n is not None:
            if not (n.get("run_id") and n.get("rel")):
                return n     # entity/disk-grafted nodes: launcher has its own fallbacks
            # LEDGER-SOURCED run-output node: its artifact_path is a server URL
            # or None — an address for browsers, never bytes. Returning it as-is
            # SHADOWED the byte-resolving fallback below (before the ledger
            # change these files weren't in the tree, so the fallback always
            # ran) — live regression: an unretained store output matched here
            # and the launcher had no source. A locally-addressable node (e.g.
            # an in-store /artifacts copy) passes through; anything else falls
            # THROUGH to the project resolver by the ledger's recorded rel —
            # a tree match must never beat byte resolution.
            from core.files.materialize import _resolve_artifact_disk_path
            src = _resolve_artifact_disk_path(n.get("artifact_path"))
            if src is not None and src.exists():
                return n
            path = n["rel"]
        # Not in the entity-graph tree — a fresh weft Run output (e.g. a `.lstar.zarr` store in
        # the live kernel jobdir). Resolve it directly from the Run's outputs (retained tree /
        # jobdir / sandbox), the same fallback the open_viewer tool uses, so launch/download work
        # without a prior data_register. This is a LOOKUP: resolve_project_run_output confirms
        # existence WITHOUT moving bytes — a remote output not yet local comes back as a marker
        # (`artifact_path` is the logical name, not an on-disk file); the viewer LAUNCH path
        # fetches it under the size gate. `..`/absolute components are refused at the resolver's
        # cache/sandbox joins (`_safe_join`), so a crafted `path=` can't read outside the caches.
        from content.bio.lifecycle.runs import resolve_project_run_output
        hit = resolve_project_run_output(path)
        if hit is not None:
            _rid, abs_path = hit
            return {"entity_id": None, "entity_type": None,
                    "name": Path(abs_path).name, "artifact_path": abs_path, "size": None,
                    "run_id": _rid}   # lets the launch route retain-on-view (P3)
        cands = list_file_matches(tree, path)
        hint = f" Did you mean: {', '.join(cands)}?" if cands else ""
        raise HTTPException(404, f"no file matching {path!r} in this project.{hint}")
    raise HTTPException(400, "supply either entity_id or path")


@router.get("/api/viewers/for")
def viewers_for_node(
    entity_id: str | None = None,
    path: str | None = None,
):
    """Return the viewer entries applicable to a tree node. Supply
    either entity_id (entity-backed file) or path (any node in the
    files tree). First entry is the default; the rest are alternates.

    The frontend uses this to build the right-click viewer menu and to
    pick the default click handler.
    """
    import content.bio  # noqa: F401 — ensure registrations
    from core.viewers.registry import viewers_for, to_wire

    node = _resolve_files_node(entity_id, path)
    viewers = viewers_for(node)
    return {
        "primary": viewers[0].id if viewers else None,
        "viewers": [to_wire(v) for v in viewers],
        "download_url": (
            f"/api/entities/{node['entity_id']}/download" if node.get("entity_id") and node.get("artifact_path")
            else f"/api/files/download?path={path}" if path
            else None
        ),
    }


class ViewerLaunchIn(BaseModel):
    entity_id: str | None = None
    path: str | None = None
    viewer_id: str | None = None      # which external viewer; default = highest-priority


@router.post("/api/viewers/launch")
def viewers_launch(body: ViewerLaunchIn, _pid: str = Depends(require_project)):
    """Start preparing an `external`-mode viewer's data store in the BACKGROUND
    and return `{job_id, label}` (viewers.md §3). The launch page
    (GET /viewer-launch) polls /api/viewers/launch/status and redirects to the
    viewer when ready — so the conversion (e.g. .h5ad → .lstar.zarr) never blocks
    this request. Errors surface as the job's error (shown on the launch page)."""
    import content.bio  # noqa: F401 — ensure viewer + launcher registrations
    from core.viewers.registry import viewers_for
    from core.viewers.launchers import launch as launch_viewer
    from core.viewers import prepare
    from core.projects import current_project_id

    node = _resolve_files_node(body.entity_id, body.path)
    ext = [v for v in viewers_for(node) if v.mode == "external" and v.open_external]
    v = (next((x for x in ext if x.id == body.viewer_id), None) if body.viewer_id
         else (ext[0] if ext else None))
    if v is None:
        raise HTTPException(404, "no external viewer applies to this file")
    pid = current_project_id()

    # P3 retain-on-view: opening a fresh weft output that isn't entity-registered
    # records a keep decision so it becomes durable. On a LIVE session kernel this
    # is a deferred pin — the bytes aren't capturable mid-life, so the viewer keeps
    # serving from the sandbox jobdir in place until settlement promotes it to the
    # retained tree (misc/output_serving_model.md P3 wording fix). Best-effort.
    if node.get("run_id") and not node.get("entity_id"):
        try:
            from content.bio.lifecycle.runs import resolve_output, set_keep_decision
            info = resolve_output(node["run_id"], Path(node["artifact_path"]).name)
            if info and info["durability"] == "live" and info.get("rel"):
                set_keep_decision(node["run_id"], keep=[info["rel"]])
        except Exception:  # noqa: BLE001 — viewing must never fail on retention
            pass

    def runner(set_phase):
        set_phase("Preparing the dataset…")
        return launch_viewer(v.open_external, node, {
            "entity_id": node.get("entity_id"), "path": body.path,
            "project_id": pid, "set_phase": set_phase,
        })
    job_id = prepare.start(runner, label=v.label or v.id)
    return {"job_id": job_id, "label": v.label or v.id}


@router.get("/api/viewers/launch/status")
def viewers_launch_status(job: str):
    """Poll a prepare job started by /api/viewers/launch."""
    from core.viewers import prepare
    s = prepare.status(job)
    if s is None:
        raise HTTPException(404, "no such prepare job")
    return s


@router.get("/api/viewers/download")
def viewers_download(
    entity_id: str | None = None,
    path: str | None = None,
    viewer_id: str | None = None,
    _pid: str = Depends(require_project),
):
    """Download an external viewer's prepared store as a single-file
    `.lstar.zarr.zip` (viewers.md §3).

    For pagoda3 this is lstar's canonical STORED single-file store (produced BY
    lstar via the launcher's `download_packer` — STORED, byte-range-readable, so it
    re-opens directly in pagoda3/lstar). View links + the internal cache stay the
    directory `.lstar.zarr` (faster to load, updatable); only this download is the
    single file. Reuses the SAME cached store the viewer opens; the archive is
    CACHED beside the store (packed once, atomically swapped, re-packed only when
    the store is re-derived) and served with `FileResponse` (Accept-Ranges/206).
    The frontend routes here THROUGH the /viewer-launch progress page
    (action=download) so the one-time store conversion runs in the background
    prepare job, not this request."""
    import content.bio  # noqa: F401 — ensure viewer + launcher registrations
    from core.viewers.registry import viewers_for
    from core.viewers.launchers import launch as launch_viewer
    from core.viewers.store_serve import zip_store_stored
    from core.projects import current_project_id

    node = _resolve_files_node(entity_id, path)
    ext = [v for v in viewers_for(node) if v.mode == "external" and v.open_external]
    v = (next((x for x in ext if x.id == viewer_id), None) if viewer_id
         else (ext[0] if ext else None))
    if v is None:
        raise HTTPException(404, "no external viewer applies to this file")
    pid = current_project_id()
    res = launch_viewer(v.open_external, node, {
        "entity_id": node.get("entity_id"), "path": path, "project_id": pid,
    })
    store = Path(res.store_path) if res.store_path else None
    if not store or not store.is_dir():
        raise HTTPException(409, "this viewer has no downloadable store")
    # Clean download name (drop the cache tag): <stem>.lstar.zarr.zip
    stem = (store.name[:-len(".lstar.zarr")] if store.name.endswith(".lstar.zarr")
            else store.stem)
    stem = stem.rsplit("-", 1)[0] if "-" in stem else stem   # strip the -<hash8> tag
    # Stable cached archive beside the store; re-pack only if missing or older than
    # the store (ensure_derived rewrites the whole dir on re-derive → newer mtime).
    pack = res.download_packer or zip_store_stored     # launcher's lstar packer, else generic STORED
    zip_path = store.with_name(store.name + ".zip")
    if not zip_path.exists() or zip_path.stat().st_mtime < store.stat().st_mtime:
        tmp = zip_path.with_name(zip_path.name + ".tmp")
        pack(store, tmp)
        tmp.replace(zip_path)                          # atomic publish
    return FileResponse(
        str(zip_path), media_type="application/zip", filename=f"{stem}.lstar.zarr.zip",
    )

