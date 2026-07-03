"""open_viewer implementation — resolve an external viewer for an entity/file
and return a `/viewer-launch` URL Guide surfaces as a chat link.

Mirrors the /api/viewers/launch selection (viewers_for → external → pick), but
does NOT start the prepare job: it just hands back the launch URL. The
/viewer-launch page (opened when the user clicks the link) runs the prepare +
poll + redirect, so Guide's turn never blocks on conversion. See
misc/pagoda3_integration.md (surfacing, Tier 2).
"""
from __future__ import annotations

import os
from urllib.parse import urlencode


def open_viewer_impl(params: dict, ctx: dict | None = None) -> dict:
    import content.bio  # noqa: F401 — ensure viewer + launcher registrations
    from core.viewers.registry import viewers_for
    from core.config import current_project_id
    from core.graph.entities import get_entity

    entity_id = (params.get("entity_id") or "").strip() or None
    file_path = (params.get("file_path") or params.get("path") or "").strip() or None
    viewer_id = (params.get("viewer_id") or "").strip() or None

    # Fall back to the focused entity so "view this" works without an explicit id.
    if not entity_id and not file_path and ctx:
        entity_id = ctx.get("focus_entity_id") or None
    if not entity_id and not file_path:
        return {"ok": False, "error": "Provide entity_id or file_path (or focus an entity first)."}

    # Build a dispatch node. Match on the artifact BASENAME (not the entity
    # title) so extension-based external viewers — pagoda3 (.h5ad / .lstar.zarr)
    # — match: viewers_for keys off `name or artifact_path`, and a title like
    # "Processed PBMC" wouldn't end in the file extension.
    if entity_id:
        e = get_entity(entity_id)
        if not e:
            return {"ok": False, "error": f"No entity {entity_id}."}
        artifact = e.get("artifact_path") or ""
        node = {
            "entity_id": e["id"],
            "entity_type": e.get("type"),
            "name": os.path.basename(artifact) if artifact else (e.get("title") or ""),
            "artifact_path": artifact,
            "size": None,
        }
    else:
        node = {
            "entity_id": None,
            "entity_type": None,
            "name": os.path.basename(file_path),
            "artifact_path": file_path,
            "size": None,
        }

    ext = [v for v in viewers_for(node) if v.mode == "external" and v.open_external]
    if not ext:
        tgt = entity_id or file_path
        return {
            "ok": False,
            "error": (
                f"No external viewer applies to {tgt!r}. pagoda3 handles single-cell "
                "results saved as .h5ad or .lstar.zarr — this file isn't one of those."
            ),
        }
    v = next((x for x in ext if x.id == viewer_id), None) if viewer_id else ext[0]
    if v is None:
        return {"ok": False, "error": f"No external viewer with id {viewer_id!r} applies here."}

    q = {"viewer": v.id, "project": current_project_id()}
    if v.label:
        q["label"] = v.label
    if entity_id:
        q["entity"] = entity_id
    else:
        q["path"] = file_path
    viewer_url = "/viewer-launch?" + urlencode(q)

    label = v.label or v.id
    return {
        "ok": True,
        "viewer_id": v.id,
        "label": label,
        "viewer_url": viewer_url,
        "_agent_hint": (
            f"Present viewer_url to the user as a markdown link — [{label}]({viewer_url}) — "
            "NOT the raw URL. The UI renders it as a launch button that opens a new tab, "
            "shows a brief 'preparing…' screen while the data store is built, then loads "
            "the interactive viewer."
        ),
    }
