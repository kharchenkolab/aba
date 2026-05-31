"""Analysis-run lifecycle (entity-model v3 'Analysis run').

A Run groups the outputs of a coherent, usually planned, analysis so they read
as one unit instead of scattering across per-turn `analysis` entities. A Run IS
an `analysis` entity (files/tree.py RUN_TYPES = {"analysis"}) tagged in metadata:

    thread_id:  <home thread>     — so the Files tree places it under the thread
    run_state:  "open" | "closed"

At most one Run is *open* per thread — the "active" one. While it's open,
harvested artifacts (lifecycle/registry._ensure_analysis) attach to it, so a
multi-turn pipeline is one Run with its figures/tables rather than a pile of
per-turn analyses.

Opened by the agent (open_run) as it begins executing an approved plan, or
rotated by the next open_run. Closed by close_run (explicit, or on a topic
pivot the agent recognizes). Closing an EMPTY Run discards it, so an abandoned
or re-planned analysis doesn't litter the tree.
"""
from __future__ import annotations
from typing import Optional

from core.graph._schema import _conn, WORKSPACE_ID
from core.graph.entities import (
    create_entity, get_entity, update_entity, archive_entity, list_entities,
)


def active_run_id(thread_id: str) -> Optional[str]:
    """The currently-open Run for this thread, or None (newest wins)."""
    if not thread_id:
        return None
    # list_entities orders by created_at asc → reverse for newest-first.
    for e in reversed(list_entities(type_filter="analysis", include_archived=False)):
        md = e.get("metadata") or {}
        if md.get("thread_id") == thread_id and md.get("run_state") == "open":
            return e["id"]
    return None


def _has_children(run_id: str) -> bool:
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM entities WHERE parent_entity_id = ? AND status != 'archived' LIMIT 1",
            (run_id,),
        ).fetchone()
    return r is not None


def run_output_dir(project_id: str, run_id: str) -> "Path":
    """The scratch working directory a Run's pipeline writes into — every file
    it produces (figures, .rds/.h5ad, subfolders) lands here, so the Run is a
    browsable output bundle. Keyed by the run's entity id."""
    from core.data.workspace import scratch_dir
    return scratch_dir(str(project_id or "default"), str(run_id))


def open_run(thread_id: str, title: str, *, focus_entity_id: Optional[str] = None,
             plan_entity_id: Optional[str] = None) -> str:
    """Open a Run for the thread, rotating out any currently-open one first
    (a new boundary supersedes the previous Run). Returns the run (analysis) id.
    Records the run's output directory as its `artifact_path` so run_python/run_r
    cd into it and the Files tree can show its full output.

    Idempotent against redundant AGENT calls: agents frequently call open_run at the
    start of an approved-plan execution even though a Run was already opened (server-
    side, on plan approval) — sometimes twice, incl. a blank-title call. Without this,
    each rotates (close+recreate), churning Runs and leaving outputs under a junk Run.
    So for an agent call (no plan_entity_id) when a Run is already open, a redundant
    request — no/blank title, or the same title as the open Run — returns the existing
    Run instead of rotating. The server's plan-boundary call (plan_entity_id set) always
    rotates, as a new approved plan should supersede the prior Run."""
    cur = active_run_id(thread_id)
    if cur and not plan_entity_id:
        norm = (title or "").strip()
        if not norm:
            return cur   # blank-title open_run → never mint a junk duplicate
        cur_title = (get_entity(cur) or {}).get("title") or ""
        if norm[:120] == cur_title[:120]:
            return cur   # same title as the already-open Run → no rotation/dup
    close_run(thread_id)
    md: dict = {"thread_id": thread_id, "run_state": "open", "origin": "internal"}
    if plan_entity_id:
        md["plan_entity_id"] = plan_entity_id
    rid = create_entity(
        entity_type="analysis",
        title=(title or "Analysis run").strip()[:120],
        parent_entity_id=focus_entity_id or WORKSPACE_ID,
        metadata=md,
    )
    try:
        from core import projects
        d = run_output_dir(projects.current() or "default", rid)
        update_entity(rid, artifact_path=str(d))
    except Exception:  # noqa: BLE001 — never block opening a run on dir setup
        pass
    return rid


def close_run(thread_id: str) -> Optional[str]:
    """Close the thread's open Run, if any. An EMPTY Run (no outputs, no
    captured code) is discarded instead of kept, so abandoned/re-planned
    analyses don't litter the tree. Returns the closed/discarded id, or None."""
    rid = active_run_id(thread_id)
    if not rid:
        return None
    ent = get_entity(rid)
    if not (ent or {}).get("producing_code") and not _has_children(rid):
        archive_entity(rid)
        return rid
    md = dict((ent or {}).get("metadata") or {})
    md["run_state"] = "closed"
    update_entity(rid, metadata=md)
    return rid


_FIG_EXT = {"png", "jpg", "jpeg", "svg", "webp", "gif", "pdf"}
_TAB_EXT = {"csv", "tsv"}
_MANIFEST_CAP = 24
# PDF-thumbnail rasterization — for the Run-view Plots grid. The agent occasionally
# saves figures as .pdf (recipe says PNG, but it drifts); without a thumb the grid
# tile is unrenderable. pypdfium2 is pure-Python + no system deps. Cached as a
# sibling .thumb.png; regenerated only if the PDF is newer than the cache.
def _pdf_thumb_path(pdf_path) -> object:
    from pathlib import Path as _P
    return _P(pdf_path).with_suffix(_P(pdf_path).suffix + ".thumb.png")


def _ensure_pdf_thumb(pdf_path) -> bool:
    """Render PDF page 1 to a sibling .thumb.png at ~600px wide. Idempotent
    (mtime-checked). Never raises — returns False if anything went wrong."""
    from pathlib import Path
    try:
        p = Path(pdf_path)
        thumb = _pdf_thumb_path(p)
        if thumb.exists() and thumb.stat().st_mtime >= p.stat().st_mtime:
            return True
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(str(p))
        if len(doc) == 0:
            return False
        page = doc[0]
        # ~600 px target — scale = 600/page_width_pt × 72dpi factor
        scale = max(0.5, min(3.0, 600 / max(50, page.get_width())))
        bitmap = page.render(scale=scale)
        bitmap.to_pil().save(thumb, "PNG", optimize=True)
        return True
    except Exception:  # noqa: BLE001 — thumbnail is best-effort; fall back to badge
        return False


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return (f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}")
        f /= 1024
    return f"{n} B"


def refresh_output_manifest(run_id: str, *, plot_urls_by_name: Optional[dict] = None) -> None:
    """Scan the Run's output directory and write a `metadata.run` manifest
    (outputs / bulk) so the Run view lists what the pipeline produced — figures
    (with thumbnails), tables, and every other file (.rds/.h5ad/…) as a
    downloadable row. Called after each cell so the Run stays current. The full
    nested directory is also browsable in the Files tree; this is the summary."""
    from pathlib import Path
    ent = get_entity(run_id)
    if not ent:
        return
    d = ent.get("artifact_path")
    if not d:
        return
    base = Path(d)
    if not base.exists():
        return
    plot_urls_by_name = plot_urls_by_name or {}
    from urllib.parse import quote
    outputs: list[dict] = []
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        try:
            sz = f.stat().st_size
        except OSError:
            continue
        rel = f.relative_to(base).as_posix()
        ext = f.suffix.lower().lstrip(".")
        url = f"/api/runs/{run_id}/file?rel={quote(rel)}"
        if ext in _FIG_EXT:
            # PDF: rasterize page 1 to a sibling .thumb.png + use that as the
            # thumb URL so the Plots grid can actually render it.
            thumb_url = plot_urls_by_name.get(f.name) or url
            if ext == "pdf" and _ensure_pdf_thumb(f):
                from urllib.parse import quote as _q
                thumb_rel = (f.relative_to(base).as_posix() + ".thumb.png")
                thumb_url = f"/api/runs/{run_id}/file?rel={_q(thumb_rel)}"
            outputs.append({"kind": "figure", "label": rel, "thumb": thumb_url,
                            "href": url, "size": _human_size(sz)})
        elif ext in _TAB_EXT:
            outputs.append({"kind": "table", "label": rel, "href": url, "size": _human_size(sz)})
        else:
            outputs.append({"kind": "file", "label": rel, "href": url + "&download=1",
                            "size": _human_size(sz)})
    bulk = None
    if len(outputs) > _MANIFEST_CAP:
        bulk = {"count": len(outputs), "note": f"{len(outputs)} files in the run folder"}
        # Keep figures + the first files; figures are the high-signal previews.
        figs = [o for o in outputs if o["kind"] == "figure"]
        rest = [o for o in outputs if o["kind"] != "figure"]
        outputs = (figs + rest)[:_MANIFEST_CAP]
    meta = dict(ent.get("metadata") or {})
    run_meta = dict(meta.get("run") or {})
    run_meta["outputs"] = outputs
    if bulk:
        run_meta["bulk"] = bulk
    else:
        run_meta.pop("bulk", None)
    meta["run"] = run_meta
    update_entity(run_id, metadata=meta)


def append_run_code(run_id: str, code: str) -> None:
    """Accumulate a cell onto the Run's producing_code — so the Run is the
    recompute/branch unit, not just a folder of figures."""
    if not run_id or not code:
        return
    ent = get_entity(run_id)
    if not ent:
        return
    block = code.strip()
    prior = ent.get("producing_code") or ""
    if not block or block in prior:
        return
    combined = (prior + "\n\n# ---\n" + block) if prior else block
    update_entity(run_id, producing_code=combined[:20000])
