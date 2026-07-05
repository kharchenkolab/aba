"""Files-tab routes — the virtual project file tree + downloads/content/raw/
AI-summary + promote-to-dataset. Moved out of main.py (Item 2A.4). All reach the
bio files tree (`content.bio.files.tree`), so they live in the bio web layer.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from core.graph.entities import get_entity
from core.web.deps import require_project_context

router = APIRouter()


@router.get("/api/files/tree")
def files_tree(include_archived: bool = False, project_id: str | None = None):
    require_project_context(project_id)
    """Virtual files view — the nested project hierarchy (files.md §3.3).
    Threads → runs/results/claims, runs → child files, results → member
    files. Multi-rooted: the same canonical artifact may appear at
    multiple paths.

    Each node carries `kind` (root/folder/file/readme), `name`, `path`,
    `entity_id` + `entity_type` (when backed by an entity), and either
    `children` (folders) or content metadata (files). READMEs carry
    their rendered Markdown inline so the UI shows the same prose the
    materialized tree would have.
    """
    import content.bio  # noqa: F401 — ensure builders register
    from content.bio.files.tree import build_files_tree
    return build_files_tree(include_archived=include_archived)


@router.get("/api/files/download")
def files_download_zip(path: str = ""):
    """Stream a ZIP of every file under the given tree path.

    Walks the nested files-tree (the same one /api/files/tree returns),
    finds the node at `path` (empty = root), and zips every file +
    readme beneath it. Real artifacts are added with their on-disk
    mtime preserved; synthesized files (READMEs, claim .md, etc.) get
    the entity's created_at as the zip-entry mtime.
    """
    import io
    import zipfile
    import content.bio  # noqa: F401 — register builders
    from content.bio.files.tree import build_files_tree, find_node, iter_files
    from core.files.materialize import _resolve_artifact_disk_path

    tree = build_files_tree(include_archived=False)
    node = find_node(tree, path)
    if node is None:
        raise HTTPException(404, f"no node at {path!r}")

    # Single file → stream it directly. (Earlier behavior zipped a one-file
    # download with an empty arcname → corrupt .zip.) Real on-disk files use
    # FileResponse so the browser gets the right MIME + filename; synthesized
    # text nodes (READMEs, claim .md bodies) stream the text body inline.
    if node.get("kind") in ("file", "readme"):
        name = node.get("name") or (path.rsplit("/", 1)[-1] if path else "file")
        if node.get("kind") == "readme":
            return Response(
                content=node.get("content") or "",
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        if node.get("synthesized"):
            return Response(
                content=node.get("synthesized_content") or "",
                media_type="text/plain; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{name}"'},
            )
        if node.get("artifact_path"):
            src = _resolve_artifact_disk_path(node["artifact_path"])
            if src and src.exists():
                return FileResponse(str(src), filename=name, media_type=None)
        raise HTTPException(404, f"file at {path!r} is not on disk")

    leaves = iter_files(node)
    if not leaves:
        raise HTTPException(404, f"no files under {path!r}")

    base = (node.get("path") or "").rstrip("/")
    base_prefix_len = len(base) + 1 if base else 0

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for leaf in leaves:
            arcname = leaf["path"][base_prefix_len:] if base_prefix_len else leaf["path"]
            mtime = leaf.get("mtime")
            if leaf["kind"] == "readme":
                _write_zip_text(zf, arcname, leaf.get("content", ""), mtime)
            elif leaf.get("synthesized"):
                _write_zip_text(zf, arcname, leaf.get("synthesized_content") or "", mtime)
            elif leaf.get("artifact_path"):
                src = _resolve_artifact_disk_path(leaf["artifact_path"])
                if src and src.exists():
                    zf.write(src, arcname=arcname)  # preserves source mtime
    buf.seek(0)
    fname = (base.rsplit("/", 1)[-1] or "files") + ".zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


def _write_zip_text(zf, arcname: str, content: str, mtime: float | None) -> None:
    """Add synthesized text content to a zip with the given mtime."""
    import zipfile, datetime
    info = zipfile.ZipInfo(filename=arcname)
    if mtime is not None:
        dt = datetime.datetime.fromtimestamp(mtime)
        info.date_time = (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, content)


@router.post("/api/files/promote")
def files_promote(path: str, title: str = ""):
    """Promote an unregistered working/scratch file into a curated Dataset entity
    (data.md scratch→curated tier). Validates that `path` is a real *ephemeral*
    working-tree node (not an arbitrary disk path), then registers its on-disk
    artifact as a dataset via the same service the agent's register_dataset uses.
    """
    import content.bio  # noqa: F401 — register tree builders + tools
    from content.bio.files.tree import build_files_tree, find_node
    from content.bio.tools import register_dataset_tool

    node = find_node(build_files_tree(include_archived=False), (path or "").strip())
    if not node or node.get("kind") != "file" or not node.get("ephemeral"):
        raise HTTPException(400, "not a promotable working file")
    ap = node.get("artifact_path")
    if not ap or not Path(ap).exists():
        raise HTTPException(404, "working file is no longer on disk")
    res = register_dataset_tool({
        "path": ap,
        "title": (title or node.get("name") or Path(ap).name).strip(),
        "summary": "Promoted from the working/scratch tier.",
        "source": "promoted-from-working",
    })
    if res.get("status") != "ok":
        raise HTTPException(400, res.get("error") or res.get("note") or "promotion failed")
    return res


@router.get("/api/files/content")
def files_content(path: str, download: int = 0):
    """Serve a tree file's RAW BYTES (with content-type) — powers the image
    viewer + binary downloads for files whose artifact_path is an on-disk path
    (run-output / working-tree files), which the browser can't fetch directly.
    Harvested entities use their served /artifacts URL instead."""
    import mimetypes
    import content.bio  # noqa: F401
    from content.bio.files.tree import build_files_tree, find_node
    from core.files.materialize import _resolve_artifact_disk_path

    tree = build_files_tree(include_archived=False)
    node = find_node(tree, path)
    if node is None:
        raise HTTPException(404, f"no node at {path!r}")
    src = _resolve_artifact_disk_path(node.get("artifact_path"))
    if src is None or not src.exists():
        raise HTTPException(404, f"file content missing on disk: {node.get('artifact_path')}")
    media = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    headers = {"Content-Disposition": f'attachment; filename="{src.name}"'} if download else {}
    return FileResponse(str(src), media_type=media, headers=headers)


@router.get("/api/files/raw")
def files_raw(path: str, offset: int = 0, max_lines: int = 200):
    """Stream a chunk of a file's text content (viewers.md fallback —
    powers CSV/TSV/JSON/text viewers in the frontend).

    `offset` and `max_lines` paginate through the file by line. Caps
    apply to the response size (~256 KB max payload) so this is safe
    against huge files. Returns:
      {lines: [...], offset, next_offset, total_lines_seen, eof,
       truncated, encoding}
    """
    import content.bio  # noqa: F401
    from content.bio.files.tree import build_files_tree, find_node
    from core.files.materialize import _resolve_artifact_disk_path

    tree = build_files_tree(include_archived=False)
    node = find_node(tree, path)
    if node is None:
        raise HTTPException(404, f"no node at {path!r}")

    # Synthesized / inline content is easy — slice the embedded text.
    inline = node.get("content") or node.get("synthesized_content")
    if inline is not None:
        all_lines = inline.splitlines()
        end = min(offset + max(1, min(max_lines, 5000)), len(all_lines))
        chunk = all_lines[offset:end]
        return {
            "lines": chunk, "offset": offset, "next_offset": end,
            "total_lines_seen": len(all_lines), "eof": end >= len(all_lines),
            "truncated": False, "encoding": "utf-8", "source": "inline",
        }

    artifact = node.get("artifact_path")
    src = _resolve_artifact_disk_path(artifact)
    if src is None or not src.exists():
        raise HTTPException(404, f"file content missing on disk: {artifact}")

    # Hard cap: refuse pulls > 256 KB of text. Lines may run long.
    cap_chars = 256 * 1024
    n = max(1, min(max_lines, 5000))
    chunk: list[str] = []
    chars = 0
    line_no = 0
    eof = False
    truncated = False
    try:
        with src.open("rb") as f:
            for raw in f:
                line_no += 1
                if line_no <= offset:
                    continue
                try:
                    s = raw.decode("utf-8")
                except UnicodeDecodeError:
                    s = raw.decode("latin-1", errors="replace")
                s = s.rstrip("\n").rstrip("\r")
                if chars + len(s) > cap_chars:
                    truncated = True
                    break
                chunk.append(s)
                chars += len(s) + 1
                if len(chunk) >= n:
                    break
            else:
                eof = True
            # Distinguish "we hit max_lines" from "real EOF".
            if not eof and not truncated and len(chunk) < n:
                eof = True
    except OSError as e:
        raise HTTPException(500, f"read failed: {e}")

    next_offset = offset + len(chunk)
    return {
        "lines": chunk,
        "offset": offset,
        "next_offset": next_offset,
        "total_lines_seen": line_no,
        "eof": eof,
        "truncated": truncated,
        "encoding": "utf-8",
        "source": "disk",
    }


class AiSummaryRequest(BaseModel):
    path: str | None = None
    entity_id: str | None = None


@router.post("/api/files/ai-summary")
def file_ai_summary(req: AiSummaryRequest):
    """AI fallback viewer (viewers.md §6.1). Reads up to 4 KB of the
    file's content (or metadata for binaries), hands it to the
    file_summarizer Agent, returns Markdown.

    For now: no cache, no PHI gate, no cost cap — those land alongside
    the consent UX. Cheap to call; an explicit user click each time.
    """
    import content.bio  # noqa: F401 — registrations
    from core.files.materialize import _resolve_artifact_disk_path
    from content.bio.files.tree import build_files_tree, find_node
    from core.runtime.agent import get_agent_spec, run_advisor_one_shot

    # Resolve the file: path-first, then entity_id.
    inline_text: str | None = None
    artifact: str | None = None
    name = ""
    if req.path:
        tree = build_files_tree(include_archived=False)
        node = find_node(tree, req.path)
        if node is None:
            raise HTTPException(404, f"no node at {req.path!r}")
        name = node.get("name") or ""
        inline_text = node.get("content") or node.get("synthesized_content") or None
        artifact = node.get("artifact_path")
    elif req.entity_id:
        e = get_entity(req.entity_id)
        if not e:
            raise HTTPException(404, f"no entity {req.entity_id}")
        name = e.get("title") or e["id"]
        artifact = e.get("artifact_path")
    else:
        raise HTTPException(400, "supply either path or entity_id")

    peek_chars = 4000
    peek = ""
    file_size = None
    if inline_text:
        peek = inline_text[:peek_chars]
    elif artifact:
        src = _resolve_artifact_disk_path(artifact)
        if src and src.exists():
            try:
                file_size = src.stat().st_size
            except OSError:
                pass
            if src.suffix.lower() in {
                ".md", ".markdown", ".txt", ".log", ".py", ".r", ".sh", ".sql",
                ".yaml", ".yml", ".json", ".ts", ".tsx", ".js", ".jsx", ".csv", ".tsv",
            }:
                try:
                    peek = src.read_text(errors="replace")[:peek_chars]
                except OSError:
                    peek = ""

    spec = get_agent_spec("file_summarizer")
    if spec is None:
        return {
            "markdown": f"_No file_summarizer agent registered._\n\nFile: `{name}` ({file_size or 'unknown'} bytes).",
            "agent": None,
        }

    prompt_parts = [f"Filename: `{name}`"]
    if file_size is not None:
        prompt_parts.append(f"Size on disk: {file_size} bytes.")
    if not peek:
        prompt_parts.append("(Binary or unreadable file — no text peek available.)")
    else:
        prompt_parts.append("Content peek (first 4 KB):")
        prompt_parts.append("```")
        prompt_parts.append(peek)
        prompt_parts.append("```")

    text = run_advisor_one_shot(spec, user_prompt="\n".join(prompt_parts), max_tokens=400)
    return {"markdown": text, "agent": "file_summarizer"}
