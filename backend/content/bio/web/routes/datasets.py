"""Dataset endpoints (/api/datasets/*, /api/upload-folder).

A Dataset is a first-class data bundle entity — either a directory of
files uploaded into the project or a by-reference handle to data living
elsewhere on disk.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from core.web.deps import require_project
from core.data.paths import unique_path as _unique_path
from core.data.paths import unique_dir_path as _unique_dir_path
from core.graph.entities import create_entity, get_entity, update_entity


router = APIRouter()


def _refresh_dataset_layout_hint(bundle: Path) -> str:
    try:
        from content.bio.tools import _dataset_layout_hint
        return _dataset_layout_hint(str(bundle))
    except Exception:
        return ""


def _dataset_bytes_and_count(bundle: Path) -> tuple[int, int]:
    total, count = 0, 0
    if not bundle.is_dir():
        return (total, count)
    for p in bundle.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
            count += 1
    return (total, count)


@router.post("/api/datasets/{did}/recheck")
def dataset_recheck(did: str, _pid: str = Depends(require_project)):
    """§5 drift banner [Re-check]: revalidate the durable home now and record
    the outcome on the entity (source_changed / source_missing + checked_at),
    so the banner and the ledger render from RECORDED state (freshness
    discipline: this is the on-demand check, never a render-time probe)."""
    import time
    ent = get_entity(did)
    if not ent or ent.get("type") != "dataset":
        raise HTTPException(404, f"no dataset {did}")
    md = dict(ent.get("metadata") or {})
    from core.data.datasets import revalidate
    out = revalidate(md)
    state = out.get("state")
    md.pop("source_changed", None)
    md.pop("source_missing", None)
    if state == "drifted":
        md["source_changed"] = True
    elif state == "missing":
        md["source_missing"] = True
    md["source_checked_at"] = int(time.time())
    update_entity(did, metadata=md)
    return {"state": state, "checked_at": md["source_checked_at"]}


class _RelinkBody(BaseModel):
    path: str
    site: str | None = None


@router.post("/api/datasets/{did}/relink")
def dataset_relink(did: str, body: _RelinkBody, _pid: str = Depends(require_project)):
    """§5 verified relink ("it moved"): accept ONLY on a content match
    (names+sizes, mtimes excluded — see core.data.datasets.relink). On match:
    the home repoints, the fingerprint refreshes, the move is noted on the
    entity. On mismatch: 409 with both shapes — the UI demotes to the
    new-version flow; we never silently repoint at different content."""
    ent = get_entity(did)
    if not ent or ent.get("type") != "dataset":
        raise HTTPException(404, f"no dataset {did}")
    md = dict(ent.get("metadata") or {})
    from core.data.datasets import relink
    out = relink(md, (body.path or "").strip(), site=body.site)
    if out.get("state") == "no_home":
        raise HTTPException(400, "this dataset has no durable home to relink")
    if out.get("state") == "missing":
        raise HTTPException(404, out.get("detail") or "nothing at the new path")
    if not out.get("ok"):
        raise HTTPException(409, {"state": "mismatch",
                                  "new_shape": out.get("new_shape"),
                                  "old_shape": out.get("old_shape"),
                                  "hint": "content differs — register it as a "
                                          "new version instead"})
    import time
    old_home = md.get("home")
    md["home"] = out["home"]
    md["fingerprint"] = out["fingerprint"]
    md.pop("source_changed", None)
    md.pop("source_missing", None)
    md["source_checked_at"] = int(time.time())
    moves = list(md.get("moves") or [])
    moves.append({"from": old_home, "to": out["home"], "at": md["source_checked_at"]})
    md["moves"] = moves
    update_entity(did, metadata=md)
    return {"ok": True, "home": out["home"]}


@router.get("/api/datasets/{did}/tree")
def dataset_tree(did: str):
    """The dataset's subtree from the files tree (its directory contents,
    or the single registered file) — so the Dataset view can browse a
    folder dataset with the shared FileBrowser.

    Adds `is_directory: bool` to the root response — the authoritative
    signal of whether the dataset is shaped as a directory on disk."""
    ent = get_entity(did)
    if not ent or ent["type"] != "dataset":
        raise HTTPException(404, f"Dataset {did} not found")
    from content.bio.files.tree import build_files_tree

    tree = build_files_tree(include_archived=False)

    def _find(node):
        if node.get("entity_id") == did:
            return node
        for c in node.get("children") or []:
            hit = _find(c)
            if hit:
                return hit
        return None

    ap = ent.get("artifact_path")
    is_directory = bool(ap) and Path(ap).is_dir()

    node = _find(tree)
    if node is None:
        return {"kind": "root", "name": ent.get("title") or "dataset",
                "path": "", "children": [], "is_directory": is_directory}
    if node.get("kind") == "folder":
        return {**node, "kind": "root", "is_directory": True}
    # Single-file dataset → present the one file under a root.
    return {"kind": "root", "name": ent.get("title") or "dataset",
            "path": "", "children": [node], "is_directory": is_directory}


@router.post("/api/datasets")
async def datasets_create(req: dict | None = None, _pid: str = Depends(require_project)):
    """Create an empty directory-shaped dataset entity. Body:
    {name?, project_id?}. The dataset folder is created on disk so
    subsequent upload-folder?append_to= calls can drop files into it."""
    from core.config import project_data_dir
    from core.projects import current_project_id
    from core.web.deps import _pin_or_412
    body = req or {}
    _pin_or_412(body.get("project_id"))
    raw = (body.get("name") or "").strip() or "New dataset"
    safe = Path(raw).name.strip() or "New dataset"
    bundle = _unique_dir_path(project_data_dir(current_project_id()) / safe)
    bundle.mkdir(parents=True, exist_ok=True)
    from core.graph.derivation import manual, human_actor
    eid = create_entity(
        entity_type="dataset", title=bundle.name, artifact_path=str(bundle),
        derivation=manual(), actor=human_actor(),   # Phase 2B: hand-created empty dataset
        metadata={"size_bytes": 0, "file_count": 0, "layout": "directory",
                  "layout_hint": "", "original_name": raw},
    )
    return get_entity(eid)


@router.post("/api/upload-folder")
async def upload_folder(
    folder_name: str = Form(...),
    files: list[UploadFile] = File(...),
    rel_paths: list[str] = Form(...),
    append_to: str | None = Form(None),
    project_id: str | None = Form(None), _pid: str = Depends(require_project)):
    """Upload N files as ONE directory-shaped dataset entity, preserving
    the folder layout. If `append_to=<dataset_id>`, files are appended
    to that existing dataset; the dataset's size/file_count/layout_hint
    are refreshed. Returns the (created or updated) entity."""
    from core.config import project_data_dir
    from core.projects import current_project_id
    from core.web.deps import _pin_or_412
    _pin_or_412(project_id)
    if not files:
        raise HTTPException(400, "no files in upload")
    if len(files) != len(rel_paths):
        raise HTTPException(400, "files and rel_paths length mismatch")

    appending = bool(append_to)
    if appending:
        existing = get_entity(append_to)
        if not existing or existing["type"] != "dataset":
            raise HTTPException(404, f"Dataset {append_to} not found")
        ap = existing.get("artifact_path") or ""
        if not ap or (Path(ap).exists() and not Path(ap).is_dir()):
            raise HTTPException(400, "cannot append to a single-file dataset")
        bundle = Path(ap)
        bundle.mkdir(parents=True, exist_ok=True)
        if (existing.get("metadata") or {}).get("layout") != "directory":
            meta = dict((existing.get("metadata") or {}))
            meta["layout"] = "directory"
            update_entity(append_to, metadata=meta)
    else:
        safe = Path(folder_name).name.strip() or "uploaded_folder"
        bundle = _unique_dir_path(project_data_dir(current_project_id()) / safe)
        bundle.mkdir(parents=True, exist_ok=True)

    written = 0
    for f, rel in zip(files, rel_paths):
        rel_clean = Path(rel).as_posix().lstrip("/")
        if not rel_clean or ".." in rel_clean.split("/"):
            continue
        dest = bundle / rel_clean
        if appending and dest.exists():
            dest = _unique_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        written += 1

    if written == 0:
        if not appending:
            try: bundle.rmdir()
            except OSError: pass
        raise HTTPException(400, "no valid file paths in upload")

    total_bytes, file_count = _dataset_bytes_and_count(bundle)
    hint = _refresh_dataset_layout_hint(bundle)

    if appending:
        meta = dict((existing.get("metadata") or {}))
        meta.update({"size_bytes": total_bytes, "file_count": file_count,
                     "layout": "directory", "layout_hint": hint})
        update_entity(append_to, metadata=meta)
        return get_entity(append_to)

    from core.graph.derivation import imported, human_actor
    eid = create_entity(
        entity_type="dataset", title=bundle.name, artifact_path=str(bundle),
        derivation=imported(folder_name), actor=human_actor(),   # Phase 2B
        metadata={"size_bytes": total_bytes, "file_count": file_count,
                  "layout": "directory", "layout_hint": hint,
                  "original_name": folder_name},
    )
    return get_entity(eid)
