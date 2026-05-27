import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import ARTIFACTS_DIR, DATA_DIR
from content.bio.graph.result_members import add_result_member, remove_result_member, update_result_member, reorder_result_members
from core.graph._schema import init_db, gen_entity_id, WORKSPACE_ID
from core.graph.edges import add_edge, remove_edge, edges_from, edges_to
from core.graph.entities import list_entities, get_entity, create_entity, update_entity, archive_entity, restore_entity
from core.graph.messages import get_messages, clear_messages
from guide import stream_response
from content.bio.lifecycle.promote import (
    promote_figure_to_result,
    promote_results_to_finding,
    promote_findings_to_claim,
    add_result_to_finding,
    remove_result_from_finding,
)
from content.bio.lifecycle.scenarios import create_scenario_variant
from content.bio.advisors.runner import skeptic_review, explorer_suggest, stylist_review
from core.graph.audit import list_advisor_notes, set_advisor_note_status, list_context_suggestions, update_context_suggestion_status
from content.bio.lifecycle.adaptive import append_to_policy, run_probe
from content.bio.tools_registry import registry as tools_registry
from content.bio.graph.figure_history import figure_history
from core.graph.audit import list_events
from core.graph.jobs import list_jobs, get_job
from core.jobs.runner import start_worker, cancel_job


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
    from core import projects
    projects.init()          # picks/creates the active project + init_db
    start_worker()


# ---------- Projects ----------

class ProjectRequest(BaseModel):
    name: str = ""


@app.get("/api/projects")
def projects_list():
    from core import projects
    return projects.list_projects()


@app.get("/api/projects/current")
def projects_current():
    from core import projects
    return {"current": projects.current()}


@app.post("/api/projects")
def projects_create(req: ProjectRequest):
    from core import projects
    return projects.create_project(req.name)


@app.post("/api/projects/{pid}/open")
def projects_open(pid: str):
    from core import projects
    projects.set_current(pid)
    return {"current": projects.current()}


@app.patch("/api/projects/{pid}")
def projects_rename(pid: str, req: ProjectRequest):
    from core import projects
    projects.rename_project(pid, req.name)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def projects_delete(pid: str):
    from core import projects
    projects.delete_project(pid)
    return {"current": projects.current()}


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
    interpretation: str | None = None   # one-line caption on a Result (merged into metadata)
    thread_id: str | None = None        # re-home a Result to another thread (merged into metadata)


@app.patch("/api/entities/{entity_id}")
def entities_patch(entity_id: str, req: EntityPatch):
    """Update title, notes, tags, pinned, or status."""
    if entity_id == WORKSPACE_ID:
        # Allow updating workspace title only; status/pin/notes/tags ignored.
        if req.title:
            updated = update_entity(entity_id, title=req.title)
            from core import projects
            projects.rename_project(projects.current(), req.title)  # keep Home registry in sync
            if updated:
                return updated
        return get_entity(entity_id)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    # interpretation / thread_id are Result metadata, not top-level columns.
    meta_updates = {}
    for k in ("interpretation", "thread_id"):
        if k in fields:
            meta_updates[k] = fields.pop(k)
    if meta_updates:
        ent = get_entity(entity_id)
        if not ent:
            raise HTTPException(404, f"Entity {entity_id} not found")
        merged = {**(ent.get("metadata") or {}), **meta_updates}
        fields["metadata"] = merged
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
    # the model's context.
    focus_entity_id: str = WORKSPACE_ID
    # The thread (line of inquiry) this turn belongs to. "default" = the
    # implicit default thread (small projects never name one).
    thread_id: str = "default"
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
            thread_id=req.thread_id,
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
def messages_list(thread_id: str | None = None):
    """The project's conversation. `thread_id` scopes to one thread
    ("default" = the default thread, materialized or not); omitted = all."""
    if thread_id == "default":
        from core.graph.threads import find_default_thread
        thread_id = find_default_thread() or "default"   # real id if materialized
    return get_messages(WORKSPACE_ID, thread_id=thread_id)


# ---------- Threads (v3 lines of inquiry) ----------

class ThreadRequest(BaseModel):
    title: str = ""
    question: str = ""
    question_source: str | None = None   # 'user' when the user typed the question


class ThreadPatch(BaseModel):
    title: str | None = None
    question: str | None = None
    open_questions: list[dict] | None = None
    lifecycle: str | None = None


@app.get("/api/threads")
def threads_list():
    from core.graph.threads import list_threads
    return list_threads()


@app.post("/api/threads")
def threads_create(req: ThreadRequest):
    from core.graph.threads import create_thread
    tid = create_thread(req.title, req.question)
    # A user-typed question is user-owned — keep the Guide from silently
    # rewriting it later.
    if req.question and req.question_source:
        ent = get_entity(tid)
        meta = dict(ent.get("metadata") or {})
        meta["question_source"] = req.question_source
        update_entity(tid, metadata=meta)
    return get_entity(tid)


@app.patch("/api/threads/{tid}")
def threads_patch(tid: str, req: ThreadPatch):
    ent = get_entity(tid)
    if not ent or ent["type"] != "thread":
        raise HTTPException(404, f"Thread {tid} not found")
    meta = dict(ent.get("metadata") or {})
    fields: dict = {}
    if req.title is not None:
        fields["title"] = req.title
    if req.question is not None:
        meta["question"] = req.question
    if req.open_questions is not None:
        meta["open_questions"] = req.open_questions
    if req.lifecycle is not None:
        meta["lifecycle"] = req.lifecycle
    fields["metadata"] = meta
    return update_entity(tid, **fields)


# ---- thread open questions (component CRUD) ----

class OpenQRequest(BaseModel):
    text: str = ""
    source: str = "user"


class OpenQPatch(BaseModel):
    text: str | None = None
    status: str | None = None      # open | parked | answered | promoted
    answer: str | None = None      # the answer captured when marking answered


def _thread_or_404(tid: str) -> dict:
    ent = get_entity(tid)
    if not ent or ent["type"] != "thread":
        raise HTTPException(404, f"Thread {tid} not found")
    return ent


def _save_oqs(tid: str, ent: dict, oqs: list):
    meta = dict(ent.get("metadata") or {})
    meta["open_questions"] = oqs
    update_entity(tid, metadata=meta)


@app.post("/api/threads/{tid}/open-questions")
def oq_add(tid: str, req: OpenQRequest):
    from core.graph._schema import gen_entity_id
    ent = _thread_or_404(tid)
    oqs = list((ent.get("metadata") or {}).get("open_questions") or [])
    oq = {"id": gen_entity_id("oq"), "text": req.text.strip(),
          "status": "open", "source": req.source,
          "at": datetime.now(timezone.utc).isoformat()}
    oqs.append(oq)
    _save_oqs(tid, ent, oqs)
    return oq


@app.patch("/api/threads/{tid}/open-questions/{oqid}")
def oq_patch(tid: str, oqid: str, req: OpenQPatch):
    ent = _thread_or_404(tid)
    oqs = list((ent.get("metadata") or {}).get("open_questions") or [])
    found = None
    for o in oqs:
        if o.get("id") == oqid:
            if req.text is not None:
                o["text"] = req.text.strip()
            if req.status is not None:
                o["status"] = req.status
            if req.answer is not None:
                o["answer"] = req.answer.strip()
            found = o
    if not found:
        raise HTTPException(404, "open question not found")
    _save_oqs(tid, ent, oqs)
    return found


@app.delete("/api/threads/{tid}/open-questions/{oqid}")
def oq_delete(tid: str, oqid: str):
    ent = _thread_or_404(tid)
    oqs = [o for o in ((ent.get("metadata") or {}).get("open_questions") or [])
           if o.get("id") != oqid]
    _save_oqs(tid, ent, oqs)
    return {"ok": True}


@app.post("/api/threads/{tid}/open-questions/{oqid}/promote")
def oq_promote(tid: str, oqid: str):
    """Promote an open question into its own thread (title + question seeded
    from the OQ); mark the source OQ promoted and link it."""
    from core.graph.threads import create_thread
    ent = _thread_or_404(tid)
    oqs = list((ent.get("metadata") or {}).get("open_questions") or [])
    oq = next((o for o in oqs if o.get("id") == oqid), None)
    if not oq:
        raise HTTPException(404, "open question not found")
    text = oq["text"]
    new_tid = create_thread(text[:60], text)
    oq["status"] = "promoted"
    oq["promoted_to"] = new_tid
    _save_oqs(tid, ent, oqs)
    return {"thread": get_entity(new_tid), "open_question": oq}


# ---------- Proactive proposals (Phase D) ----------

class EvaluateRequest(BaseModel):
    trigger: str = "post_turn"


@app.get("/api/threads/{tid}/proposals")
def thread_proposals(tid: str, status: str = "pending"):
    from core.graph.proposals_store import list_proposals
    rtid = _resolve_thread(tid)
    return list_proposals(thread_id=rtid, status=(status or None))


@app.post("/api/threads/{tid}/evaluate")
def thread_evaluate(tid: str, req: EvaluateRequest):
    """Run the proposal detectors for a thread on demand (used by the
    thread-open event trigger). Post-turn evaluation is fired from guide.py."""
    from content.bio.proposals.scheduler import evaluate_thread
    from core.graph.proposals_store import list_proposals
    rtid = _resolve_thread(tid)
    evaluate_thread(rtid, req.trigger)
    return list_proposals(thread_id=rtid, status="pending")


@app.post("/api/threads/{tid}/orient")
def thread_orient(tid: str):
    """Cold-start orientation: the Guide summarizes the project's data + suggests
    next steps as an opening message. Idempotent — no-ops once the thread has a
    conversation or has already been oriented."""
    from content.bio.lifecycle.orientation import orient_thread
    rtid = _resolve_thread(tid)
    result = orient_thread(rtid)
    return {"oriented": bool(result), "result": result}


@app.post("/api/proposals/{pid}/accept")
def proposal_accept(pid: int):
    from content.bio.proposals.scheduler import accept_proposal
    try:
        return accept_proposal(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/proposals/{pid}/dismiss")
def proposal_dismiss(pid: int):
    from content.bio.proposals.scheduler import dismiss_proposal
    return dismiss_proposal(pid)


@app.post("/api/proposals/{pid}/undo")
def proposal_undo(pid: int):
    from content.bio.proposals.scheduler import undo_proposal
    try:
        return undo_proposal(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------- Runs (analysis runs) ----------

class PinOutputRequest(BaseModel):
    kind: str = "figure"
    label: str = ""
    thumb: str | None = None
    href: str | None = None
    size: str | None = None
    interpretation: str = ""


def _run_or_404(rid: str) -> dict:
    e = get_entity(rid)
    if not e or e["type"] != "analysis":
        raise HTTPException(404, f"Run {rid} not found")
    return e


@app.post("/api/runs/{rid}/cancel")
def run_cancel(rid: str):
    e = _run_or_404(rid)
    meta = dict(e.get("metadata") or {})
    run = dict(meta.get("run") or {})
    run["status"] = "cancelled"
    run["finished_at"] = _now()
    meta["run"] = run
    return update_entity(rid, metadata=meta)


@app.post("/api/runs/{rid}/pin-output")
def run_pin_output(rid: str, req: PinOutputRequest):
    """Pin one of a run's outputs into the thread as a Result. Plots/tables we
    can render are kept with their thumbnail; everything else is a *reference*
    (origin=external + href) — we don't host a copy."""
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id")
    etype = "table" if req.kind == "table" else "figure"
    is_img = bool(req.thumb) and req.thumb.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "svg", "webp", "gif")
    eid = create_entity(
        entity_type=etype, title=req.label or "result",
        artifact_path=(req.thumb if is_img else None),
        metadata={"thread_id": tid, "origin": "external", "interpretation": req.interpretation,
                  "source_run": rid, "href": req.href, "out_kind": req.kind})
    update_entity(eid, pinned=True)
    add_edge(eid, rid, "produced_by")
    return get_entity(eid)


class RegisterDatasetRequest(BaseModel):
    label: str = ""
    path: str | None = None       # filesystem path / href the bundle lives at
    size: str | None = None
    summary: str = ""


@app.post("/api/runs/{rid}/register-dataset")
def run_register_dataset(rid: str, req: RegisterDatasetRequest):
    """Lift a run's PRIMARY artifact (e.g. a processed-data bundle) into a
    first-class Dataset entity — by reference: we record where it lives, we do
    not host a copy. Once registered it feeds downstream runs like any dataset."""
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id")
    eid = create_entity(
        entity_type="dataset", title=req.label or "dataset",
        metadata={"thread_id": tid, "origin": "external", "by_reference": True,
                  "ref_path": req.path, "size_label": req.size, "summary": req.summary,
                  "source_run": rid})
    add_edge(eid, rid, "produced_by")
    return get_entity(eid)


# ---------- Results (kept observations — a grouping of panels) ----------

class MemberRequest(BaseModel):
    kind: str = "figure"            # figure | table | value | text
    ref: str | None = None         # cell entity id (for figure/table/value)
    text: str | None = None        # inline prose (for text panels)
    caption: str = ""
    at: int | None = None          # insert position (append if None)


class CreateResultRequest(BaseModel):
    thread_id: str = "default"
    title: str = "Result"
    interpretation: str = ""
    origin: str = "internal"
    members: list[MemberRequest] = []


def _result_or_404(rid: str) -> dict:
    e = get_entity(rid)
    if not e or e["type"] != "result":
        raise HTTPException(404, f"Result {rid} not found")
    return e


@app.post("/api/results")
def create_result(req: CreateResultRequest):
    """Create a kept Result (an observation). Usually seeded with one cell;
    grows deliberately via add-member. The single-member case is the common one."""
    eid = create_entity(
        entity_type="result", title=req.title,
        metadata={"thread_id": req.thread_id, "origin": req.origin,
                  "interpretation": req.interpretation, "members": []})
    update_entity(eid, pinned=True)
    for m in req.members:
        add_result_member(eid, kind=m.kind, ref=m.ref, text=m.text, caption=m.caption, at=m.at)
        if m.ref:
            add_edge(eid, m.ref, "includes")
    return get_entity(eid)


@app.post("/api/results/{rid}/members")
def result_add_member(rid: str, req: MemberRequest):
    _result_or_404(rid)
    out = add_result_member(rid, kind=req.kind, ref=req.ref, text=req.text, caption=req.caption, at=req.at)
    if req.ref:
        add_edge(rid, req.ref, "includes")
    return out


@app.patch("/api/results/{rid}/members/{member_id}")
def result_update_member(rid: str, member_id: str, req: MemberRequest):
    _result_or_404(rid)
    return update_result_member(rid, member_id, caption=req.caption, text=req.text)


@app.delete("/api/results/{rid}/members/{member_id}")
def result_remove_member(rid: str, member_id: str):
    e = _result_or_404(rid)
    ref = next((m.get("ref") for m in (e.get("metadata") or {}).get("members", []) if m.get("id") == member_id), None)
    out = remove_result_member(rid, member_id)
    if ref:
        remove_edge(rid, ref, "includes")
    return out


class ReorderRequest(BaseModel):
    order: list[str] = []


@app.post("/api/results/{rid}/reorder")
def result_reorder(rid: str, req: ReorderRequest):
    _result_or_404(rid)
    return reorder_result_members(rid, req.order)


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
    thread_id: str = "default"     # the note belongs to the current thread


@app.post("/api/messages/pin")
def pin_message(req: PinMessageRequest):
    """Keep any chat message: snapshot it as a lightweight 'note' entity that
    shows in the thread's pinned shelf. Toggles by content key (idempotent)."""
    from content.bio.graph.search import find_kept_note
    from core.graph.entities import update_entity
    existing = find_kept_note(req.key)
    if existing:
        update_entity(existing, status="archived")   # unpin
        return {"pinned": False}
    tid = req.thread_id
    if tid == "default":
        from core.graph.threads import get_or_create_default_thread
        tid = get_or_create_default_thread()
    title = (req.title or req.text).strip().split("\n")[0][:70] or "Kept note"
    eid = create_entity(
        entity_type="note", title=title,
        metadata={"source_key": req.key, "text": req.text,
                  "image_urls": req.image_urls, "thread_id": tid, "origin": "internal"},
    )
    update_entity(eid, pinned=True, notes=req.text[:500])
    return {"pinned": True, "id": eid}


@app.get("/api/search")
def search_endpoint(q: str = "", limit: int = 25):
    """Faceted search across entities + chat snippets (M9 fallback recovery)."""
    from content.bio.graph.search import search as _search
    return _search(q, limit=limit)


# ---------- Claims (v3 — the rigor core) ----------

CONFIDENCE = ("preliminary", "supported", "validated", "contested", "refuted")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim_or_404(cid: str) -> dict:
    ent = get_entity(cid)
    if not ent or ent["type"] != "claim":
        raise HTTPException(404, f"Claim {cid} not found")
    return ent


def _save_claim(cid: str, ent: dict, updates: dict) -> dict:
    meta = dict(ent.get("metadata") or {})
    meta.update(updates)
    return update_entity(cid, metadata=meta)


def _resolve_thread(thread_id: str) -> str:
    if thread_id == "default":
        from core.graph.threads import get_or_create_default_thread
        return get_or_create_default_thread()
    return thread_id


class ClaimRequest(BaseModel):
    statement: str = ""
    evidence_ids: list[str] = []
    thread_id: str = "default"
    negative: bool = False


class ClaimPatch(BaseModel):
    statement: str | None = None
    negative: bool | None = None


class EvidenceRequest(BaseModel):
    result_id: str


class CaveatRequest(BaseModel):
    text: str = ""
    source: str = "user"


class CaveatPatch(BaseModel):
    text: str | None = None
    dismissed: bool | None = None
    rationale: str | None = None


class AltRequest(BaseModel):
    text: str = ""
    source: str = "user"


class AltPatch(BaseModel):
    text: str | None = None
    status: str | None = None       # open | dismissed
    rationale: str | None = None


class StatusRequest(BaseModel):
    to: str
    reason: str = ""


@app.post("/api/claims")
def claim_create(req: ClaimRequest):
    tid = _resolve_thread(req.thread_id)
    stmt = req.statement.strip() or "Untitled claim"
    cid = create_entity(
        entity_type="claim", title=stmt[:80],
        metadata={"statement": stmt, "negative": req.negative,
                  "evidence_ids": list(req.evidence_ids), "caveats": [], "alternatives": [],
                  "confidence": "preliminary", "thread_id": tid,
                  "status_log": [{"from": None, "to": "preliminary", "reason": "created",
                                  "actor": "user", "at": _now()}]})
    for rid in req.evidence_ids:
        add_edge(cid, rid, "supports")
    return get_entity(cid)


@app.patch("/api/claims/{cid}")
def claim_patch(cid: str, req: ClaimPatch):
    ent = _claim_or_404(cid)
    upd: dict = {}
    if req.statement is not None:
        upd["statement"] = req.statement.strip()
    if req.negative is not None:
        upd["negative"] = req.negative
    _save_claim(cid, ent, upd)
    if req.statement is not None:
        update_entity(cid, title=req.statement.strip()[:80])
    return get_entity(cid)


@app.post("/api/claims/{cid}/evidence")
def claim_add_evidence(cid: str, req: EvidenceRequest):
    ent = _claim_or_404(cid)
    ev = list((ent.get("metadata") or {}).get("evidence_ids") or [])
    if req.result_id not in ev:
        ev.append(req.result_id)
        add_edge(cid, req.result_id, "supports")
    return _save_claim(cid, ent, {"evidence_ids": ev})


@app.delete("/api/claims/{cid}/evidence/{rid}")
def claim_del_evidence(cid: str, rid: str):
    ent = _claim_or_404(cid)
    ev = [x for x in ((ent.get("metadata") or {}).get("evidence_ids") or []) if x != rid]
    remove_edge(cid, rid, "supports")
    return _save_claim(cid, ent, {"evidence_ids": ev})


@app.post("/api/claims/{cid}/caveats")
def claim_add_caveat(cid: str, req: CaveatRequest):
    ent = _claim_or_404(cid)
    cavs = list((ent.get("metadata") or {}).get("caveats") or [])
    cav = {"id": gen_entity_id("cav"), "text": req.text.strip(),
           "source": req.source, "dismissed": False, "at": _now()}
    cavs.append(cav)
    _save_claim(cid, ent, {"caveats": cavs})
    return cav


@app.patch("/api/claims/{cid}/caveats/{caid}")
def claim_patch_caveat(cid: str, caid: str, req: CaveatPatch):
    ent = _claim_or_404(cid)
    cavs = list((ent.get("metadata") or {}).get("caveats") or [])
    found = None
    for c in cavs:
        if c.get("id") == caid:
            if req.text is not None:
                c["text"] = req.text.strip()
            if req.dismissed is not None:
                c["dismissed"] = req.dismissed
            if req.rationale is not None:
                c["rationale"] = req.rationale
            found = c
    if not found:
        raise HTTPException(404, "caveat not found")
    _save_claim(cid, ent, {"caveats": cavs})
    return found


@app.delete("/api/claims/{cid}/caveats/{caid}")
def claim_del_caveat(cid: str, caid: str):
    ent = _claim_or_404(cid)
    cavs = [c for c in ((ent.get("metadata") or {}).get("caveats") or []) if c.get("id") != caid]
    _save_claim(cid, ent, {"caveats": cavs})
    return {"ok": True}


@app.post("/api/claims/{cid}/alternatives")
def claim_add_alt(cid: str, req: AltRequest):
    ent = _claim_or_404(cid)
    alts = list((ent.get("metadata") or {}).get("alternatives") or [])
    alt = {"id": gen_entity_id("alt"), "text": req.text.strip(),
           "source": req.source, "status": "open", "at": _now()}
    alts.append(alt)
    _save_claim(cid, ent, {"alternatives": alts})
    return alt


@app.patch("/api/claims/{cid}/alternatives/{aid}")
def claim_patch_alt(cid: str, aid: str, req: AltPatch):
    ent = _claim_or_404(cid)
    alts = list((ent.get("metadata") or {}).get("alternatives") or [])
    found = None
    for a in alts:
        if a.get("id") == aid:
            if req.text is not None:
                a["text"] = req.text.strip()
            if req.status is not None:
                a["status"] = req.status
            if req.rationale is not None:
                a["rationale"] = req.rationale
            found = a
    if not found:
        raise HTTPException(404, "alternative not found")
    _save_claim(cid, ent, {"alternatives": alts})
    return found


@app.post("/api/claims/{cid}/alternatives/{aid}/promote")
def claim_promote_alt(cid: str, aid: str):
    """Promote a competing explanation into its own claim, in the same thread."""
    ent = _claim_or_404(cid)
    meta = ent.get("metadata") or {}
    alts = list(meta.get("alternatives") or [])
    alt = next((a for a in alts if a.get("id") == aid), None)
    if not alt:
        raise HTTPException(404, "alternative not found")
    new = claim_create(ClaimRequest(statement=alt["text"], thread_id=meta.get("thread_id", "default")))
    alt["status"] = "promoted"
    alt["promoted_to"] = new["id"]
    _save_claim(cid, ent, {"alternatives": alts})
    return {"claim": new, "alternative": alt}


@app.delete("/api/claims/{cid}/alternatives/{aid}")
def claim_del_alt(cid: str, aid: str):
    ent = _claim_or_404(cid)
    alts = [a for a in ((ent.get("metadata") or {}).get("alternatives") or []) if a.get("id") != aid]
    _save_claim(cid, ent, {"alternatives": alts})
    return {"ok": True}


@app.post("/api/claims/{cid}/status")
def claim_status(cid: str, req: StatusRequest):
    if req.to not in CONFIDENCE:
        raise HTTPException(400, f"invalid confidence: {req.to}")
    ent = _claim_or_404(cid)
    meta = dict(ent.get("metadata") or {})
    frm = meta.get("confidence")
    # Rigor guard: can't mint a 'validated' claim without a robustness note.
    if req.to == "validated" and not req.reason.strip():
        raise HTTPException(400, "validated requires a robustness note (reason)")
    log = list(meta.get("status_log") or [])
    log.append({"from": frm, "to": req.to, "reason": req.reason.strip(),
                "actor": "user", "at": _now()})
    meta["confidence"] = req.to
    meta["status_log"] = log
    return update_entity(cid, metadata=meta)


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
    from core.graph.entities import list_entities as _le
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
    from content.bio.lifecycle.promote import create_finding_from_draft
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
    from content.bio.lifecycle.promote import set_finding_fields
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
    from core.graph.provenance import neighborhood
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
    # The "data added" reaction (Guide orientation) is driven by the frontend via
    # POST /api/threads/:id/orient after the upload lands — one synchronous source,
    # so the client can reload the chat + chips and there's no duplicate-post race.
    return get_entity(eid)


@app.post("/api/results/external")
async def upload_external_result(
    file: UploadFile = File(...),
    thread_id: str = Form("default"),
    interpretation: str = Form(""),
):
    """Bring in an external result (a gel, a wet-lab readout, a figure from
    another tool) as a first-class, pinned Result — identical to an internal
    one in the UI, with origin recorded for provenance."""
    if not file.filename:
        raise HTTPException(400, "filename missing")
    dest = _unique_path(ARTIFACTS_DIR / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = thread_id
    if tid == "default":
        from core.graph.threads import get_or_create_default_thread
        tid = get_or_create_default_thread()
    eid = create_entity(
        entity_type="figure", title=Path(file.filename).stem,
        artifact_path=f"/artifacts/{dest.name}",
        metadata={"original_name": file.filename, "origin": "external",
                  "thread_id": tid, "interpretation": interpretation},
    )
    update_entity(eid, pinned=True)
    # Data-upload event trigger (Phase D): new evidence may overlap a run behind
    # active claims → N+1 proposal.
    from content.bio.proposals.scheduler import evaluate_thread
    evaluate_thread(tid, "data_upload")
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
    from core.graph.tool_settings import set_tool_enabled
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


@app.get("/api/turns")
def turns_list(limit: int = 50):
    """Recent Turn checkpoints (arch3_plan.md Pass E). For diagnostic
    inspection; the resume endpoint lives at /api/turns/{run_id}/resume
    once the full state-machine extraction (Pass F) lands."""
    from core.runtime.checkpoint import list_recent_turns
    return list_recent_turns(limit=limit)


@app.get("/api/turns/{run_id}")
def turn_get(run_id: str):
    """Single-Turn lookup — what state was the loop in, what's pending."""
    from core.runtime.checkpoint import load_turn
    t = load_turn(run_id)
    if t is None:
        raise HTTPException(404, "no such run")
    return t.to_row()


@app.get("/api/health")
def health():
    return {"ok": True}
