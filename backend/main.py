import asyncio
import shutil
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import ARTIFACTS_DIR, DATA_DIR
from db import (
    init_db,
    get_messages,
    clear_messages,
    list_entities,
    get_entity,
    create_entity,
    update_entity,
    archive_entity,
    restore_entity,
    add_edge,
    edges_from,
    edges_to,
    WORKSPACE_ID,
)
from guide import stream_response
from promote import (
    promote_figure_to_result,
    promote_results_to_finding,
    promote_findings_to_claim,
    add_result_to_finding,
    remove_result_from_finding,
)
from scenarios import create_scenario_variant
from advisors import skeptic_review, explorer_suggest, stylist_review
from db import (
    list_advisor_notes,
    set_advisor_note_status,
    list_context_suggestions,
    update_context_suggestion_status,
)
from adaptive import append_to_policy, run_probe
from tools_registry import registry as tools_registry
from db import list_jobs, get_job, figure_history, list_events
from jobs import start_worker, cancel_job


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/artifacts", StaticFiles(directory=str(ARTIFACTS_DIR)), name="artifacts")


@app.on_event("startup")
def startup():
    init_db()
    start_worker()


# ---------- Entities ----------

@app.get("/api/entities")
def entities_list(
    q: str | None = None,
    type: str | None = None,
    include_archived: bool = True,
    limit: int | None = None,
    offset: int = 0,
):
    """
    Project tree feed. Workspace root is included unless filtered out.
    Pagination via limit/offset; left None by default so small projects
    don't pay any cost.
    """
    return list_entities(
        title_query=q,
        type_filter=type,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )


@app.get("/api/entities/{entity_id}")
def entities_get(entity_id: str):
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return e


class EntityPatch(BaseModel):
    title: str | None = None
    notes: str | None = None
    tags: list[str] | None = None
    pinned: bool | None = None
    status: str | None = None


@app.patch("/api/entities/{entity_id}")
def entities_patch(entity_id: str, req: EntityPatch):
    """Update title, notes, tags, pinned, or status."""
    if entity_id == WORKSPACE_ID:
        # Allow updating workspace title only; status/pin/notes/tags ignored.
        if req.title:
            updated = update_entity(entity_id, title=req.title)
            if updated:
                return updated
        return get_entity(entity_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # status whitelist
    if "status" in fields and fields["status"] not in (
        "active", "running", "superseded", "failed", "archived",
    ):
        raise HTTPException(400, f"invalid status: {fields['status']}")
    updated = update_entity(entity_id, **fields)
    if not updated:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return updated


@app.delete("/api/entities/{entity_id}")
def entities_delete(entity_id: str):
    """Soft-delete (status='archived'). Workspace cannot be deleted."""
    if entity_id == WORKSPACE_ID:
        raise HTTPException(400, "workspace cannot be deleted")
    updated = archive_entity(entity_id)
    if not updated:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return updated


@app.post("/api/entities/{entity_id}/restore")
def entities_restore(entity_id: str):
    updated = restore_entity(entity_id)
    if not updated:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return updated


@app.get("/api/entities/{entity_id}/download")
def entities_download(entity_id: str):
    """Stream the underlying artifact (figure PNG or dataset file)."""
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    if not e.get("artifact_path"):
        raise HTTPException(400, "entity has no artifact to download")
    path_str = e["artifact_path"]
    # Figures stored as URLs like /artifacts/abc.png — translate to disk.
    if path_str.startswith("/artifacts/"):
        from config import ARTIFACTS_DIR
        path = ARTIFACTS_DIR / Path(path_str).name
    else:
        path = Path(path_str)
    if not path.exists():
        raise HTTPException(404, "artifact file is missing on disk")
    # Suggest a reasonable filename based on the entity's title.
    base = e["title"].replace("/", "_").strip()
    suffix = path.suffix or ""
    download_name = f"{base}{suffix}" if base else path.name
    return FileResponse(
        path,
        filename=download_name,
        media_type=None,  # let starlette guess
    )


@app.get("/api/entities/{entity_id}/messages")
def entities_messages(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return get_messages(entity_id)


@app.delete("/api/entities/{entity_id}/messages")
def entities_clear_messages(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    clear_messages(entity_id)
    return {"ok": True}


@app.get("/api/entities/{entity_id}/preview")
def entities_preview(entity_id: str, limit: int = 20, offset: int = 0):
    """
    Return a lightweight preview for an entity's artifact, with pagination.

    Currently supported types:
      - dataset / table (CSV/TSV): `limit` rows starting at `offset` + column
        names + total row count.
    Returns {"kind": "table", "columns": [...], "rows": [[...], ...],
             "total_rows": N, "offset": offset, "shown": k}
    or {"kind": "none"} if no preview is available.
    """
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")

    offset = max(0, offset)
    if e["type"] in ("dataset", "table") and e["artifact_path"]:
        raw = e["artifact_path"]
        # Tables are stored as /artifacts/<id>.csv; datasets as disk paths.
        if raw.startswith("/artifacts/"):
            from config import ARTIFACTS_DIR
            path = ARTIFACTS_DIR / Path(raw).name
        else:
            path = Path(raw)
        if path.suffix.lower() in (".csv", ".tsv") and path.exists():
            try:
                import pandas as pd
                sep = "," if path.suffix.lower() == ".csv" else "\t"
                # Skip `offset` data rows but keep the header row (row 0).
                skip = range(1, offset + 1) if offset > 0 else None
                df = pd.read_csv(path, sep=sep, skiprows=skip, nrows=limit)
                # Get total row count without re-reading the whole frame.
                with path.open("r") as f:
                    total = sum(1 for _ in f) - 1
                return {
                    "kind": "table",
                    "columns": [str(c) for c in df.columns],
                    "rows": df.astype(object).where(df.notna(), None).values.tolist(),
                    "total_rows": max(total, 0),
                    "offset": offset,
                    "shown": len(df),
                }
            except Exception as ex:  # noqa: BLE001
                return {"kind": "error", "error": str(ex)}

    return {"kind": "none"}


# ---------- Chat ----------

class ChatRequest(BaseModel):
    text: str
    # The entity the user is *focused on* (chip / canvas). Used to augment
    # the model's context. The chat thread itself is always project-level.
    focus_entity_id: str = WORKSPACE_ID
    # Spatial reference (Phase 25): base64 PNG of the figure with the user's
    # annotation composited on, plus a short note describing the gesture.
    annotation_image: str | None = None
    annotation_note: str | None = None
    # Regenerate the last turn's reply without appending a new user message
    # (used by the message-level retry after a transient API failure).
    retry: bool = False


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not get_entity(req.focus_entity_id):
        raise HTTPException(404, f"Entity {req.focus_entity_id} not found")

    async def event_stream():
        async for chunk in stream_response(
            req.text,
            focus_entity_id=req.focus_entity_id,
            annotation_image=req.annotation_image,
            annotation_note=req.annotation_note,
            retry=req.retry,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/messages")
def messages_list():
    """The project's running conversation (workspace thread)."""
    return get_messages(WORKSPACE_ID)


@app.get("/api/entities/{entity_id}/suggest-interpretation")
def suggest_interpretation(entity_id: str):
    """Best-guess interpretation for promoting a figure → result: reuse the
    interpretation Guide already gave in chat (the assistant text right after
    the figure's tool result). Zero extra tokens."""
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    art = e.get("artifact_path")
    msgs = get_messages(WORKSPACE_ID)

    def asst_text(m):
        if m["role"] != "assistant":
            return ""
        return " ".join(b.get("text", "") for b in m["content"]
                        if isinstance(b, dict) and b.get("type") == "text").strip()

    # Locate the message whose tool_result produced this figure.
    prod_idx = None
    for i, m in enumerate(msgs):
        for blk in m["content"]:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_result":
                try:
                    plots = (json.loads(blk["content"]) or {}).get("plots") or []
                    if any(p.get("url") == art for p in plots):
                        prod_idx = i
                except Exception:
                    pass
            elif blk.get("type") == "image" and blk.get("url") == art:
                prod_idx = i

    text = ""
    if prod_idx is not None:
        for j in range(prod_idx, min(prod_idx + 4, len(msgs))):  # the interpreting turn follows
            t = asst_text(msgs[j])
            if t:
                text = t
                break
    if not text:  # fallback: most recent assistant text
        for m in reversed(msgs):
            t = asst_text(m)
            if t:
                text = t
                break
    return {"text": text[:400]}


class PinMessageRequest(BaseModel):
    key: str                       # stable content hash from the client
    text: str = ""
    title: str = ""
    image_urls: list[str] = []


@app.post("/api/messages/pin")
def pin_message(req: PinMessageRequest):
    """Keep any chat message: snapshot it as a lightweight 'note' entity that
    shows in the Pinned shelf. Toggles by content key (idempotent)."""
    from db import find_kept_note, update_entity
    existing = find_kept_note(req.key)
    if existing:
        update_entity(existing, status="archived")   # unpin
        return {"pinned": False}
    title = (req.title or req.text).strip().split("\n")[0][:70] or "Kept note"
    eid = create_entity(
        entity_type="note", title=title,
        metadata={"source_key": req.key, "text": req.text, "image_urls": req.image_urls},
    )
    update_entity(eid, pinned=True, notes=req.text[:500])
    return {"pinned": True, "id": eid}


@app.get("/api/search")
def search_endpoint(q: str = "", limit: int = 25):
    """Faceted search across entities + chat snippets (M9 fallback recovery)."""
    from db import search as _search
    return _search(q, limit=limit)


# ---------- Promotion / result chain ----------

class PromoteFigureRequest(BaseModel):
    interpretation: str
    title: str | None = None


class PromoteResultsRequest(BaseModel):
    result_ids: list[str]
    text: str
    title: str | None = None


class PromoteFindingsRequest(BaseModel):
    finding_ids: list[str]
    text: str
    title: str | None = None


@app.post("/api/entities/{figure_id}/promote-to-result")
async def promote_to_result(figure_id: str, req: PromoteFigureRequest):
    try:
        rid = promote_figure_to_result(figure_id, req.interpretation, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Fire the Skeptic asynchronously so the user gets the result back
    # promptly. The note shows up when they next reload the advisor rail
    # (or when the focus changes — the rail re-fetches).
    asyncio.get_event_loop().run_in_executor(None, skeptic_review, rid)
    return get_entity(rid)


@app.get("/api/entities/{entity_id}/advisor-notes")
def entities_advisor_notes(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return list_advisor_notes(entity_id)


class AdvisorNoteStatusRequest(BaseModel):
    status: str = "dismissed"


@app.post("/api/advisor-notes/{note_id}/status")
def advisor_note_status(note_id: int, req: AdvisorNoteStatusRequest):
    """Mark a note tried/dismissed so it no longer surfaces as a fresh idea."""
    if not set_advisor_note_status(note_id, req.status):
        raise HTTPException(404, f"Note {note_id} not found")
    return {"ok": True}


@app.post("/api/entities/{entity_id}/advise")
async def entities_advise(entity_id: str):
    """
    Fire the appropriate on-focus advisor for an entity (Explorer for
    datasets, Stylist for narratives). Idempotent — advisors that have
    already spoken about the entity won't re-fire. Non-blocking.
    """
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    loop = asyncio.get_event_loop()
    if e["type"] == "dataset":
        loop.run_in_executor(None, explorer_suggest, entity_id)
    elif e["type"] == "narrative":
        loop.run_in_executor(None, stylist_review, entity_id)
    return {"ok": True}


# ---------- Adaptive context (§3.6) ----------

@app.get("/api/context-suggestions")
def context_suggestions(status: str = "pending"):
    """List context-policy suggestions awaiting review (or any status)."""
    return list_context_suggestions(status=status)


class SuggestionAction(BaseModel):
    action: str  # 'approve' | 'reject'


@app.post("/api/context-suggestions/{sid}/action")
def context_suggestion_action(sid: int, req: SuggestionAction):
    """
    Apply a reviewer action to a suggestion:
      approve → status='promoted' + append to the per-type policy file
      reject  → status='rejected'
    """
    if req.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")
    # Fetch current suggestion to know the entity_type for policy append.
    pending = [s for s in list_context_suggestions(status=None) if s["id"] == sid]
    if not pending:
        raise HTTPException(404, f"suggestion {sid} not found")
    suggestion = pending[0]
    if req.action == "approve":
        append_to_policy(suggestion["entity_type"], suggestion["suggestion"])
        update_context_suggestion_status(sid, "promoted")
    else:
        update_context_suggestion_status(sid, "rejected")
    return {"ok": True}


@app.post("/api/findings")
def create_finding(req: PromoteResultsRequest):
    try:
        fid = promote_results_to_finding(req.result_ids, req.text, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(fid)


@app.post("/api/claims")
def create_claim(req: PromoteFindingsRequest):
    try:
        cid = promote_findings_to_claim(req.finding_ids, req.text, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(cid)


class NarrativeRequest(BaseModel):
    title: str
    text: str = ""


@app.post("/api/narratives")
def create_narrative(req: NarrativeRequest):
    eid = create_entity(
        entity_type="narrative",
        title=req.title or "Untitled section",
        metadata={"text": req.text},
    )
    return get_entity(eid)


class FindingResultRequest(BaseModel):
    result_id: str


class DraftFindingRequest(BaseModel):
    text: str = ""                 # concatenated text of the selected messages
    title_hint: str = ""           # e.g. the first user message in the selection
    image_urls: list[str] = []     # figure/plot urls seen in the selection


@app.post("/api/findings/draft")
def draft_finding(req: DraftFindingRequest):
    """Selection-to-finding draft (M3). Heuristic for now (no tokens): title
    from the ask, summary from the discussion, evidence resolved from the
    figures referenced in the selection. The user reviews before saving."""
    from db import list_entities as _le
    text = req.text.strip()
    first = (req.title_hint or text).strip().split("\n")[0]
    title = (first[:80] + ("…" if len(first) > 80 else "")) or "Untitled finding"
    summary = text[:600] + ("…" if len(text) > 600 else "")
    urls = set(req.image_urls or [])
    evidence = []
    if urls:
        for e in _le():
            if e.get("artifact_path") in urls and e["type"] in ("figure", "table"):
                evidence.append({"id": e["id"], "type": e["type"], "title": e["title"]})
    return {"title": title, "summary": summary, "evidence": evidence, "caveats": []}


class CreateFindingRequest(BaseModel):
    title: str
    summary: str = ""
    evidence_ids: list[str] = []
    caveats: list[dict] = []
    status: str = "candidate"


@app.post("/api/findings/from-draft")
def create_finding_endpoint(req: CreateFindingRequest):
    from promote import create_finding_from_draft
    fid = create_finding_from_draft(req.title, req.summary, req.evidence_ids,
                                    req.caveats, req.status)
    return get_entity(fid)


class FindingFieldsRequest(BaseModel):
    summary: str | None = None
    caveats: list[dict] | None = None
    status: str | None = None
    title: str | None = None


@app.post("/api/findings/{finding_id}/fields")
def finding_fields(finding_id: str, req: FindingFieldsRequest):
    from promote import set_finding_fields
    try:
        return set_finding_fields(finding_id, summary=req.summary,
                                  caveats=req.caveats, status=req.status, title=req.title)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/findings/{finding_id}/add-result")
def finding_add_result(finding_id: str, req: FindingResultRequest):
    try:
        return add_result_to_finding(finding_id, req.result_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/findings/{finding_id}/remove-result")
def finding_remove_result(finding_id: str, req: FindingResultRequest):
    try:
        return remove_result_from_finding(finding_id, req.result_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/entities/{entity_id}/edges")
def entities_edges(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return {
        "outgoing": edges_from(entity_id),
        "incoming": edges_to(entity_id),
    }


@app.get("/api/entities/{entity_id}/history")
def entities_history(entity_id: str):
    """Version chain for a figure (newest first)."""
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return figure_history(entity_id)


@app.get("/api/entities/{entity_id}/provenance")
def entities_provenance(entity_id: str):
    """Upstream/downstream neighborhood for the canvas Provenance panel."""
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    from provenance import neighborhood
    return neighborhood(entity_id)


# ---------- Scenarios ----------

class ScenarioRequest(BaseModel):
    description: str
    # Optional: skip the LLM rewrite by supplying the new code directly.
    code: str | None = None
    title: str | None = None


@app.post("/api/entities/{baseline_id}/create-scenario")
async def create_scenario(baseline_id: str, req: ScenarioRequest):
    try:
        new_entity = await asyncio.get_event_loop().run_in_executor(
            None,
            create_scenario_variant,
            baseline_id, req.description, req.code, req.title,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return new_entity


# ---------- Upload ----------

def _unique_path(dest: Path) -> Path:
    """Suffix the filename if it already exists in the dir."""
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    i = 1
    while True:
        candidate = dest.parent / f"{stem}_{i}{suf}"
        if not candidate.exists():
            return candidate
        i += 1


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Drop an uploaded file into DATA_DIR, register as a 'dataset' entity."""
    if not file.filename:
        raise HTTPException(400, "filename missing")
    safe_name = Path(file.filename).name
    dest = _unique_path(DATA_DIR / safe_name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    size = dest.stat().st_size
    eid = create_entity(
        entity_type="dataset",
        title=dest.name,
        artifact_path=str(dest),
        metadata={"size_bytes": size, "original_name": file.filename},
    )
    return get_entity(eid)


class URLUploadRequest(BaseModel):
    url: str
    title: str | None = None


@app.post("/api/upload-url")
async def upload_url(req: URLUploadRequest):
    """
    Download a file from a URL into DATA_DIR and register a dataset entity.

    The Guide can later inspect/unpack the file (e.g. tar.gz of a 10x folder)
    via tool calls. This endpoint just lands the bytes locally.
    """
    import urllib.parse
    import urllib.request
    import urllib.error
    parsed = urllib.parse.urlparse(req.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "only http(s) URLs are supported")
    name = Path(parsed.path).name or "downloaded.bin"
    dest = _unique_path(DATA_DIR / name)

    # CDNs (Cloudflare etc.) often reject the default Python-urllib UA.
    req_obj = urllib.request.Request(
        req.url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; ABA/0.1; +bioinformatics)"},
    )
    try:
        with urllib.request.urlopen(req_obj, timeout=120) as resp:
            total = 0
            with dest.open("wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
                    if total > 2 * 1024 * 1024 * 1024:
                        raise HTTPException(413, "remote file > 2GB; aborting")
    except urllib.error.HTTPError as e:
        raise HTTPException(400, f"download failed: HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        raise HTTPException(400, f"download failed: {e}")

    eid = create_entity(
        entity_type="dataset",
        title=req.title or dest.name,
        artifact_path=str(dest),
        metadata={
            "size_bytes": dest.stat().st_size,
            "source_url": req.url,
            "original_name": name,
        },
    )
    return get_entity(eid)


# ---------- Legacy aliases (kept until frontend migration is done) ----------

@app.get("/api/history")
def history_legacy():
    return get_messages(WORKSPACE_ID)


@app.delete("/api/history")
def history_clear_legacy():
    clear_messages(WORKSPACE_ID)
    return {"ok": True}


@app.get("/api/tools")
def tools_catalog():
    """Catalog of tools and skills for the Skills screen (Phase 12/14)."""
    return tools_registry()


class ToolEnabledRequest(BaseModel):
    enabled: bool


@app.post("/api/tools/{name}/enabled")
def tools_set_enabled(name: str, req: ToolEnabledRequest):
    from db import set_tool_enabled
    set_tool_enabled(name, req.enabled)
    return {"name": name, "enabled": req.enabled}


# ---------- Jobs (Phase 17) ----------

@app.get("/api/jobs")
def jobs_list(limit: int = 50):
    return list_jobs(limit=limit)


@app.get("/api/jobs/{job_id}")
def jobs_get(job_id: str):
    j = get_job(job_id)
    if not j:
        raise HTTPException(404, f"job {job_id} not found")
    return j


@app.post("/api/jobs/{job_id}/cancel")
def jobs_cancel(job_id: str):
    ok = cancel_job(job_id)
    if not ok:
        raise HTTPException(400, "job not found or not cancellable")
    return get_job(job_id)


@app.get("/api/home-summary")
def home_summary():
    """Dashboard data for the Home screen: counts, recent activity, attention."""
    ents = list_entities(exclude_workspace=True, include_archived=False)
    counts: dict[str, int] = {}
    for e in ents:
        if e["status"] in ("superseded",):
            continue
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    jobs = list_jobs(limit=100)
    suggestions = list_context_suggestions(status="pending")
    # advisor notes across all entities
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


@app.post("/api/sample-project")
def sample_project():
    """
    One-click sample: register the bundled cells.csv as a dataset so a new
    user has something to explore immediately. Idempotent-ish (creates a
    fresh dataset each call).
    """
    src = Path(__file__).parent / "data" / "cells.csv"
    if not src.exists():
        raise HTTPException(500, "sample data missing")
    dest = _unique_path(DATA_DIR / "sample_cells.csv")
    shutil.copyfile(src, dest)
    eid = create_entity(
        entity_type="dataset", title=dest.name, artifact_path=str(dest),
        metadata={"size_bytes": dest.stat().st_size, "sample": True},
    )
    return get_entity(eid)


@app.post("/api/run-probe")
async def trigger_probe():
    """
    Run one pop-quiz probe (§3.6). Normally a background cron; exposed as an
    endpoint so it can be triggered on demand / tested. Non-blocking work
    runs in a thread.
    """
    report = await asyncio.get_event_loop().run_in_executor(None, run_probe)
    if report is None:
        return {"ran": False, "reason": "no probeable entities yet"}
    return {"ran": True, **report}


@app.get("/api/events")
def events_list(limit: int = 50, offset: int = 0):
    """Activity / audit feed (newest first)."""
    return list_events(limit=limit, offset=offset)


@app.get("/api/health")
def health():
    return {"ok": True}
