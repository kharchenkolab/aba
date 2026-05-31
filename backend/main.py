import asyncio
import json
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
from core.graph.audit import list_advisor_notes, set_advisor_note_status, list_context_suggestions, update_context_suggestion_status, reject_all_pending_suggestions
from content.bio.lifecycle.adaptive import append_to_policy, run_probe
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

import threading

# Per-project artifacts (post 2026-05-31 reorg) live under
# projects/<pid>/artifacts/<name>. The URL scheme is /artifacts/<pid>/<name> —
# served by the route handler below. Legacy single-dir mount removed.
def _artifact_url_to_path(url: str) -> Path | None:
    """Resolve an `/artifacts/...` URL stored in an entity record to a disk path.
    Returns None if the URL doesn't match the expected shape or escapes a project
    boundary. Single source of truth for URL→file mapping across handlers."""
    if not url or not url.startswith("/artifacts/"):
        return None
    parts = url[len("/artifacts/"):].split("/")
    if len(parts) == 2 and parts[0] and parts[1] and ".." not in parts[0] and ".." not in parts[1]:
        # New per-project shape: /artifacts/<pid>/<name>
        from core.config import project_artifacts_dir
        return project_artifacts_dir(parts[0]) / parts[1]
    if len(parts) == 1 and parts[0] and ".." not in parts[0]:
        # Legacy workspace-level fallback: /artifacts/<name>
        return ARTIFACTS_DIR / parts[0]
    return None


@app.get("/artifacts/{pid}/{name}")
def serve_artifact(pid: str, name: str):
    from core.config import project_artifacts_dir
    if "/" in name or ".." in name or "/" in pid or ".." in pid:
        raise HTTPException(400, "invalid artifact path")
    f = project_artifacts_dir(pid) / name
    if not f.is_file():
        raise HTTPException(404, f"artifact {pid}/{name} not found")
    return FileResponse(str(f))


@app.on_event("startup")
async def startup():
    from core import projects
    projects.init()          # picks/creates the active project + init_db
    start_worker()
    # Capture the asyncio loop so worker-thread producers
    # (auto_interpret, background jobs) can push events to the
    # /api/notifications SSE channel.
    from core.runtime import notifications as _notif
    _notif.set_loop(asyncio.get_event_loop())

    # Background-provision the curated shared R base (r_base.yaml: Seurat,
    # DESeq2/limma/edger/apeglm, tidyverse, cairo, Rcpp*). When everything
    # is already in the tools env, this completes in ~500ms (two
    # `micromamba list --json` calls, no solve). When the env is missing a
    # package, the solve + install runs in this thread — backend stays
    # responsive throughout. Daemon thread = dies with the process; never
    # blocks startup.
    def _provision_r_base_bg():
        import time as _t
        try:
            from content.bio.capabilities import provision_r_base
            t0 = _t.perf_counter()
            provision_r_base()
            dt = _t.perf_counter() - t0
            if dt > 5:
                print(f"[r_base] provisioned curated shared R base in {dt:.0f}s", flush=True)
            else:
                print(f"[r_base] curated shared R base already provisioned ({dt*1000:.0f}ms)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[r_base] provision failed (non-fatal — agent can still per-project install): {e}", flush=True)
    threading.Thread(target=_provision_r_base_bg, name="r_base_provision", daemon=True).start()
    # Pass-E follow-up: any Turn rows in GENERATING/EXECUTING_TOOLS/
    # SUMMARIZING state are from a process that didn't survive; they
    # cannot be resumed (stream + tool dispatch are in-memory). Mark
    # them FAILED so the UI doesn't show stale "in-flight" turns.
    try:
        from core.runtime.checkpoint import reap_stale_turns
        n = reap_stale_turns()
        if n:
            print(f"[startup] reaped {n} stale Turn row(s) from previous process")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] reap_stale_turns failed: {e}")
    # F3: backfill display_path for any entity created before the column
    # existed (or before bio's layout computers were registered).
    try:
        from content.bio.graph.display import backfill_missing_display_paths
        n = backfill_missing_display_paths()
        if n:
            print(f"[startup] backfilled display_path for {n} entit{'y' if n == 1 else 'ies'}")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] display_path backfill failed: {e}")

    # P3 #1 — bring up the MCP gateway. Empty config = no-op.
    try:
        from core.runtime.mcp import start_all as start_mcp, status as mcp_status
        from pathlib import Path
        start_mcp(Path(__file__).parent / "content" / "bio" / "mcp" / "servers.yaml")
        s = mcp_status()
        n_up = sum(1 for srv in s["servers"] if srv["state"] == "connected")
        n_tot = len(s["servers"])
        if n_tot:
            print(f"[startup] MCP gateway: {n_up}/{n_tot} servers connected")
    except Exception as e:  # noqa: BLE001
        print(f"[startup] MCP gateway init failed: {e}")


@app.on_event("shutdown")
async def shutdown():
    """Cancel any in-flight Turn tasks before the worker exits.

    C-1 spawns the agent loop as a background asyncio task via
    turn_executor.start_turn — without this hook, uvicorn's `--reload`
    SIGTERM hangs indefinitely because the task is awaiting a thread-pool
    future (run_in_executor) that Python can't interrupt. We fire the
    cancel token (which the loop checks at every iteration boundary) so
    each task gets a chance to commit its in-progress state and exit
    cleanly, then give them a brief window. Anything still pending after
    that gets task.cancel() as a hard stop. The startup reaper will mark
    any survivors FAILED on next boot, so we don't leak Turn rows."""
    import asyncio
    from core.runtime import turn_sink, cancellation
    rids = turn_sink.active_ids()
    if not rids:
        return
    print(f"[shutdown] cancelling {len(rids)} in-flight Turn task(s): {rids}")
    # 1. Fire cancel tokens — co-operative shutdown if the loop is at an
    #    iteration boundary or inside a cancellable tool.
    for rid in rids:
        tok = cancellation.get(rid)
        if tok is not None:
            try: tok.cancel(reason="backend shutdown")
            except Exception: pass    # noqa: BLE001
    # 2. Give them a short grace period to land.
    tasks = [s._task for s in (turn_sink.get(rid) for rid in rids)
             if s is not None and s._task is not None and not s._task.done()]
    if tasks:
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True),
                                   timeout=3.0)
        except asyncio.TimeoutError:
            print(f"[shutdown] {sum(1 for t in tasks if not t.done())} task(s) "
                  f"didn't honor cancel — forcing task.cancel()")
            # 3. Hard cancel — the next startup's reaper will tidy the DB.
            for t in tasks:
                if not t.done():
                    t.cancel()


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
    interpretation_origin: str | None = None  # 'ai' | 'user' — flips to 'user' on first edit (merged into metadata)
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
    for k in ("interpretation", "interpretation_origin", "thread_id"):
        if k in fields:
            meta_updates[k] = fields.pop(k)
    # Any user edit to a Result — title, interpretation — flips `invested`
    # so a later unpin preserves the user's work instead of archiving it
    # as an unused auto-wrapper. Caption/text edits on members flip it via
    # update_result_member (result_members.py); this catches the
    # entity-level edits.
    user_edited_result = False
    if meta_updates or ("title" in fields):
        ent_probe = get_entity(entity_id)
        if ent_probe and ent_probe.get("type") == "result":
            user_edited_result = True
    if meta_updates or user_edited_result:
        ent = get_entity(entity_id)
        if not ent:
            raise HTTPException(404, f"Entity {entity_id} not found")
        merged = {**(ent.get("metadata") or {}), **meta_updates}
        if user_edited_result:
            merged["invested"] = True
        fields["metadata"] = merged
    # status whitelist
    if "status" in fields and fields["status"] not in (
        "active", "running", "superseded", "failed", "archived",
    ):
        raise HTTPException(400, f"invalid status: {fields['status']}")
    updated = update_entity(entity_id, **fields)
    # F3: re-derive display_path when the title changes (or first time).
    if updated and "title" in fields:
        from content.bio.graph.display import recompute_display_path
        recompute_display_path(entity_id)
        updated = get_entity(entity_id)
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
    # Figures stored as URLs like /artifacts/<pid>/abc.png — translate to disk.
    resolved = _artifact_url_to_path(path_str)
    path = resolved if resolved is not None else Path(path_str)
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
def entities_messages(entity_id: str, thread_id: str | None = None):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return get_messages(entity_id, thread_id=thread_id) if thread_id else get_messages(entity_id)


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
        # Tables are stored as /artifacts/<pid>/<id>.csv; datasets as disk paths.
        resolved = _artifact_url_to_path(raw)
        if resolved is not None:
            path = resolved
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

    # C-1: spawn the agent loop as a background task that owns its own
    # lifetime; the HTTP response is just a subscriber on the resulting
    # TurnSink. Client disconnect unsubscribes; the task keeps running.
    # Reattach via GET /api/turns/{run_id}/stream?since=<lastSeq>.
    from core.runtime import turn_executor, turn_sink as _ts
    from datetime import datetime, timezone
    run_id = turn_executor.new_run_id()
    started_at = datetime.now(timezone.utc).isoformat()
    body_gen = stream_response(
        req.text,
        focus_entity_id=req.focus_entity_id,
        thread_id=req.thread_id,
        annotation_image=req.annotation_image,
        annotation_note=req.annotation_note,
        retry=req.retry,
        run_id=run_id,
    )
    sink = turn_executor.start_turn(
        run_id=run_id,
        thread_id=req.thread_id,
        started_at=started_at,
        body_gen=body_gen,
    )
    return StreamingResponse(
        _ts.stream_from_sink(sink, since=0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/runs/{rid}/refresh-manifest")
def runs_refresh_manifest(rid: str):
    """Re-scan a Run's output dir and rebuild its manifest. Useful after a
    server-side change to the manifester (e.g. new PDF-thumbnail support)."""
    from content.bio.lifecycle.runs import refresh_output_manifest
    e = get_entity(rid)
    if not e or e.get("type") != "analysis":
        raise HTTPException(404, f"run {rid} not found")
    refresh_output_manifest(rid)
    return {"ok": True}


@app.post("/api/results/{rid}/regenerate-interpretation")
def regenerate_interpretation(rid: str):
    """Re-fire the auto-interpret background job for a single Result. Used when
    the original schedule failed (e.g. the pre-fix sync-endpoint bug). Idempotent:
    auto_interpret skips if interpretation_origin=='user' (the user has edited)."""
    from content.bio.lifecycle.promote import auto_interpret
    r = get_entity(rid)
    if not r or r.get("type") != "result":
        raise HTTPException(404, f"result {rid} not found")
    text = auto_interpret(rid)
    return {"ok": True, "wrote": bool(text), "preview": (text or "")[:200]}


@app.post("/api/admin/backfill-tool-result-thread")
def backfill_tool_result_threads():
    """One-shot data fix: tool_result rows were appended with thread_id=NULL by a
    long-standing bug at guide.py:941/993 (fixed in 2026-05-31). They became
    invisible once /api/messages started filtering by thread_id. This backfills
    each orphan's thread_id from the immediately-preceding assistant message
    (same workspace) — that's the message whose tool_use the result is for."""
    from core.graph._schema import WORKSPACE_ID
    from core.graph.messages import _conn  # type: ignore[attr-defined]
    with _conn() as c:
        # Find every tool_result-bearing user row with NULL thread, and the
        # latest preceding assistant row's thread_id in the same workspace.
        rows = c.execute("""
          SELECT m.id, (
            SELECT a.thread_id FROM messages a
            WHERE a.entity_id = m.entity_id AND a.role='assistant'
              AND a.id < m.id AND a.thread_id IS NOT NULL
            ORDER BY a.id DESC LIMIT 1
          ) AS prev_thread
          FROM messages m
          WHERE m.role='user' AND m.thread_id IS NULL AND m.content LIKE '%"tool_result"%'
          AND m.entity_id = ?
        """, (WORKSPACE_ID,)).fetchall()
        updates = [(r["prev_thread"], r["id"]) for r in rows if r["prev_thread"]]
        c.executemany("UPDATE messages SET thread_id = ? WHERE id = ?", updates)
        c.commit()
    return {"backfilled": len(updates), "scanned": len(rows)}


@app.get("/api/dev/last-turn-context")
def dev_last_turn_context(thread_id: str | None = None):
    """Latest turn's FULL API context (system prompt + tools + history + user_text)
    as the model received it, read from the JSON sidecar dumped by
    guide.py:_dump_turn_context. If thread_id is set, returns the latest matching
    that thread; else just the most recent. Powers the drawer's Context tab."""
    import os, glob, json as _json
    d = os.environ.get("ABA_TURN_LOG_DIR", "/tmp/aba_turnlog")
    if d.strip().lower() in ("", "off", "0", "false"):
        raise HTTPException(404, "turn-log dir disabled")
    files = sorted(glob.glob(os.path.join(d, "*_run_*.json")), reverse=True)
    for f in files:
        try:
            payload = _json.load(open(f))
        except Exception:  # noqa: BLE001
            continue
        if thread_id and payload.get("thread_id") != thread_id:
            continue
        return payload
    raise HTTPException(404, "no turn context dumped yet")


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
    """Pin one of a run's outputs as a Result wrapping the evidence (figure/table).
    Plots/tables we can render are kept with their thumbnail; everything else is a
    *reference* (origin=external + href) — we don't host a copy."""
    from content.bio.lifecycle.promote import pin_evidence
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id") or ""
    etype = "table" if req.kind == "table" else "figure"
    is_img = bool(req.thumb) and req.thumb.lower().rsplit(".", 1)[-1] in ("png", "jpg", "jpeg", "svg", "webp", "gif")
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


@app.get("/api/datasets/{did}/tree")
def dataset_tree(did: str):
    """The dataset's subtree from the files tree (its directory contents, or the
    single registered file) — so the Dataset view can browse a folder dataset
    with the shared FileBrowser instead of showing one opaque 'file' row."""
    ent = get_entity(did)
    if not ent or ent["type"] != "dataset":
        raise HTTPException(404, f"Dataset {did} not found")
    import content.bio  # noqa: F401
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

    node = _find(tree)
    if node is None:
        return {"kind": "root", "name": ent.get("title") or "dataset", "path": "", "children": []}
    if node.get("kind") == "folder":
        return {**node, "kind": "root"}
    # Single-file dataset → present the one file under a root.
    return {"kind": "root", "name": ent.get("title") or "dataset", "path": "",
            "children": [node]}


@app.get("/api/runs/{rid}/tree")
def run_tree(rid: str):
    """The Run's subtree from the files tree (its readme, code, output/ dir +
    curated figures/tables) — so the Run view can embed the shared FileBrowser
    and browse nested output folders, not just a flat list."""
    _run_or_404(rid)
    import content.bio  # noqa: F401
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
        # Run exists but isn't placed in the tree yet (e.g. no outputs) — empty root.
        return {"kind": "root", "name": "", "path": "", "children": []}
    return {**node, "kind": "root"}   # present the run folder as the browser root


@app.get("/api/runs/{rid}/file")
def run_file(rid: str, rel: str, download: int = 0):
    """Serve a single file from a Run's output directory (its artifact_path).
    Powers the Run view's output rows + figure thumbnails. `rel` is the path
    relative to the run dir; traversal outside the dir is rejected. Images/text
    render inline; `download=1` forces an attachment."""
    import mimetypes
    from pathlib import Path
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


# ---------- Results (kept observations — a grouping of panels) ----------

class MemberRequest(BaseModel):
    kind: str = "figure"            # figure | table | value | text
    ref: str | None = None         # cell entity id (for figure/table/value)
    text: str | None = None        # inline prose (for text panels)
    caption: str = ""
    caption_origin: str | None = None   # 'ai' | 'user' — set by the UI on first edit
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
    """Create a Result (an observation). Usually seeded with one cell; grows
    deliberately via add-member. Results are on the shelf by virtue of being
    Results — no explicit pinned flag needed."""
    eid = create_entity(
        entity_type="result", title=req.title,
        metadata={"thread_id": req.thread_id, "origin": req.origin,
                  "interpretation": req.interpretation,
                  "interpretation_origin": "user",
                  "members": []})
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
    return update_result_member(rid, member_id, caption=req.caption,
                                text=req.text, caption_origin=req.caption_origin)


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
    """Generate a structured figure caption for promoting a figure → result.

    Calls the live LLM with VISION + the figure's producing_code + nearby chat
    context. The caption has two sections:
      - **What's shown** — panel-by-panel description (axes, encoding, notations).
      - **Take-home** — 1-3 bullets stating what the figure demonstrates.

    Earlier behavior just plucked nearby chat text ("Perfect! Now you have...")
    which produced chat-flavored prose unfit for a permanent record. The
    text-pluck remains as a fallback if the LLM call fails or vision can't read
    the file."""
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    art = e.get("artifact_path") or ""
    msgs = get_messages(WORKSPACE_ID)

    def asst_text(m):
        if m["role"] != "assistant":
            return ""
        return " ".join(b.get("text", "") for b in m["content"]
                        if isinstance(b, dict) and b.get("type") == "text").strip()

    # Locate the message whose tool_result produced this figure — used for
    # nearby chat context AND for the text-pluck fallback.
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

    # Gather conversation context: a window of user + assistant turns AROUND
    # the figure — user messages frame the ASK + intent (which the LLM can't
    # recover from the image alone); assistant text before the figure carries
    # plan/method context; assistant text after the figure carries the
    # interpretation in chat. Skip tool_use / tool_result / image blocks
    # (those are noise here — the figure itself + producing_code carry the
    # mechanical info). Walk a ±6-turn window around the figure, cap at ~3k
    # chars to bound token use.
    def turn_text(m):
        if m["role"] == "user":
            parts = []
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "text":
                    parts.append(b.get("text", ""))
                elif isinstance(b, str):
                    parts.append(b)
            return " ".join(p for p in parts if p).strip()
        if m["role"] == "assistant":
            return asst_text(m)
        return ""
    chat_context = ""
    if prod_idx is not None:
        lo, hi = max(0, prod_idx - 6), min(len(msgs), prod_idx + 4)
        chunks: list[str] = []
        for j in range(lo, hi):
            t = turn_text(msgs[j])
            if not t: continue
            role = msgs[j]["role"]
            tag = "USER" if role == "user" else "AGENT"
            anchor = " (← figure here)" if j == prod_idx else ""
            chunks.append(f"[{tag}{anchor}] {t}")
        chat_context = "\n\n".join(chunks)[:3000]

    producing_code = (e.get("producing_code") or "")[:6000]
    title = (e.get("title") or "").strip()

    # Try the vision-LLM path first.
    text = _llm_figure_caption(art, producing_code, chat_context, title)

    # Fallback to text-pluck if the LLM path didn't produce a usable caption.
    if not text:
        if prod_idx is not None:
            for j in range(prod_idx, min(prod_idx + 4, len(msgs))):
                t = asst_text(msgs[j])
                if t: text = t; break
        if not text:
            for m in reversed(msgs):
                t = asst_text(m)
                if t: text = t; break
    return {"text": text[:1200]}


def _llm_figure_caption(artifact_path: str, producing_code: str,
                        chat_context: str, title: str) -> str:
    """Thin wrapper around the shared vision-LLM caption helper in
    `content/bio/lifecycle/promote.py` — resolves the /artifacts/<pid>/<name>
    URL to a disk path, then delegates. Kept here so the FastAPI endpoint
    `/api/entities/<id>/suggest-interpretation` and the background
    `auto_interpret` daemon share the SAME caption generator and system
    prompt (single source of truth)."""
    from content.bio.lifecycle.promote import caption_via_vision_llm
    disk = _artifact_url_to_path(artifact_path) if artifact_path else None
    return caption_via_vision_llm(disk, producing_code, chat_context, title)


class PinMessageRequest(BaseModel):
    key: str                       # stable content hash from the client
    text: str = ""
    title: str = ""
    image_urls: list[str] = []
    thread_id: str = "default"     # the note belongs to the current thread


@app.post("/api/messages/pin")
def pin_message(req: PinMessageRequest):
    """Pin a chat message: create a Note from the text+image_urls and wrap it in a
    Result. Toggles by content key — re-pinning the same message archives its Note
    (and the unpin logic in task #321 will handle the wrapping Result transitively)."""
    from content.bio.graph.search import find_kept_note
    from content.bio.lifecycle.promote import pin_evidence
    from core.graph.entities import update_entity
    existing = find_kept_note(req.key)
    if existing:
        update_entity(existing, status="archived")   # unpin (B will refine: also archive the wrapping Result)
        return {"pinned": False}
    tid = req.thread_id
    if tid == "default":
        from core.graph.threads import get_or_create_default_thread
        tid = get_or_create_default_thread()
    title = (req.title or req.text).strip().split("\n")[0][:70] or "Kept note"
    out = pin_evidence(
        thread_id=tid, target_result_id=None,
        evidence_kind="note",
        evidence_payload={
            "title": title,
            "metadata": {"source_key": req.key, "text": req.text, "image_urls": req.image_urls},
        },
        interpretation=req.text[:500] or None,   # message text doubles as the initial interpretation
        origin="internal",
    )
    return {"pinned": True, "id": out["evidence_id"], "result_id": out["result_id"]}


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
def context_suggestions(status: str = "pending", max_age_days: int = 14):
    """List context-policy suggestions awaiting review (or any status).

    `max_age_days` defaults to 14 — older pending items auto-stale out
    of the badge / list so they don't sit in the drawer forever. Pass
    0 (or any non-positive) to include every age."""
    return list_context_suggestions(
        status=status,
        max_age_days=max_age_days if max_age_days > 0 else None,
    )


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
    # Pass max_age_days=None so we can act on stale items too.
    pending = [s for s in list_context_suggestions(status=None, max_age_days=None) if s["id"] == sid]
    if not pending:
        raise HTTPException(404, f"suggestion {sid} not found")
    suggestion = pending[0]
    if req.action == "approve":
        append_to_policy(suggestion["entity_type"], suggestion["suggestion"])
        update_context_suggestion_status(sid, "promoted")
    else:
        update_context_suggestion_status(sid, "rejected")
    return {"ok": True}


@app.post("/api/context-suggestions/reject-all")
def context_suggestion_reject_all():
    """Bulk-reject every pending suggestion (any age). One click instead
    of N. Returns the count rejected."""
    return {"rejected": reject_all_pending_suggestions()}


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
    """Drop an uploaded file into the active project's data dir + register as a
    'dataset' entity."""
    if not file.filename:
        raise HTTPException(400, "filename missing")
    safe_name = Path(file.filename).name
    from core.config import current_project_id, project_data_dir
    dest = _unique_path(project_data_dir(current_project_id()) / safe_name)
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
    another tool) as a first-class Result wrapping the uploaded figure."""
    from content.bio.lifecycle.promote import pin_evidence
    if not file.filename:
        raise HTTPException(400, "filename missing")
    from core.config import current_project_id, project_artifacts_dir
    pid = current_project_id()
    dest = _unique_path(project_artifacts_dir(pid) / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = thread_id
    if tid == "default":
        from core.graph.threads import get_or_create_default_thread
        tid = get_or_create_default_thread()
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
    from content.bio.proposals.scheduler import evaluate_thread
    evaluate_thread(tid, "data_upload")
    return get_entity(out["result_id"])


# ── NEW pin endpoints (A2) ─────────────────────────────────────────────────────

@app.post("/api/entities/{entity_id}/pin")
def pin_entity_to_result(entity_id: str):
    """EntityMenu Pin: promote this existing evidence entity (figure/table/cell/
    note/narrative) into a Result. Result is created immediately with a
    ✨-placeholder interpretation; a background job (A3) replaces it with the
    Guide's adjacent narration."""
    from content.bio.lifecycle.promote import pin_evidence, auto_interpret
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    if ent["type"] in ("result", "claim", "finding"):
        raise HTTPException(400, f"{ent['type']} is curation; not pinnable")
    tid = (ent.get("metadata") or {}).get("thread_id") or ""
    out = pin_evidence(
        thread_id=tid, target_result_id=None,
        evidence_kind=ent["type"], evidence_id=entity_id,
        interpretation=None, origin=(ent.get("metadata") or {}).get("origin", "internal"),
    )
    # NB: this endpoint is SYNC, so asyncio.get_event_loop() doesn't work here
    # (FastAPI runs it on a worker thread w/o a loop). Use a plain Thread to fire
    # the background interpretation job — fire-and-forget, daemon so it doesn't
    # block shutdown. The same pattern works in async endpoints too.
    import threading
    threading.Thread(target=auto_interpret, args=(out["result_id"],), daemon=True).start()
    return get_entity(out["result_id"])


@app.post("/api/entities/{entity_id}/unpin")
def unpin_entity(entity_id: str):
    """Inverse of /pin — applies the unpin branching (B / #321): archive the
    wrapping Result(s) if this is the only evidence, else just remove this
    evidence as a member. See lifecycle.promote.unpin_evidence."""
    from content.bio.lifecycle.promote import unpin_evidence
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    tid = (ent.get("metadata") or {}).get("thread_id")
    return unpin_evidence(entity_id, thread_id=tid)


@app.post("/api/results/{rid}/upload-evidence")
async def result_upload_evidence(
    rid: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    """Result-page Add-evidence: upload a file and append it as a NEW member of
    this existing Result. Interpretation is NOT regenerated — the Result keeps
    its existing description; the new evidence is added beneath it."""
    from content.bio.lifecycle.promote import pin_evidence
    r = _result_or_404(rid)
    if not file.filename:
        raise HTTPException(400, "filename missing")
    from core.config import current_project_id, project_artifacts_dir
    pid = current_project_id()
    dest = _unique_path(project_artifacts_dir(pid) / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = (r.get("metadata") or {}).get("thread_id") or ""
    out = pin_evidence(
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
    from core.config import current_project_id, project_data_dir
    dest = _unique_path(project_data_dir(current_project_id()) / name)

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
    from core.config import current_project_id, project_data_dir
    dest = _unique_path(project_data_dir(current_project_id()) / "sample_cells.csv")
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


@app.get("/api/notifications")
async def notifications_stream():
    """Global SSE channel for OUT-OF-BAND events (caption ready, background
    job done, entity updated, …) — things that happen outside the chat
    turn lifecycle. Frontend opens this once on app mount and refreshes
    on relevant events instead of guessing refresh intervals.

    Event shape: `{"type": "entity_updated", "entity_id": "...", "reason": "..."}`.
    A "hello" event fires on connect so the client knows the stream is live.
    """
    from core.runtime import notifications as _notif

    async def gen():
        q = _notif.subscribe()
        try:
            yield f"data: {json.dumps({'type': 'hello'})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies / load balancers from closing
                    # the idle connection.
                    yield ": heartbeat\n\n"
        finally:
            _notif.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


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


@app.get("/api/turns/{run_id}/stream")
async def turn_stream(run_id: str, since: int = 0):
    """C-1 reattach: subscribe to an in-flight Turn's event sink and
    stream its events as SSE. Replays any events with seq > since from
    the in-memory tail, then live-streams new ones. Heartbeats every
    ~25s keep the connection alive through idle periods.

    Client disconnect just unsubscribes — the agent loop is untouched
    and the next reconnect with `?since=<lastSeq>` resumes from where
    the client left off.

    Returns 410 Gone if the sink isn't in the registry (process restart
    or evicted by future C-2 sweeper) AND the Turn is already terminal
    in the DB — nothing to subscribe to and nothing to replay.
    Returns 404 if the run_id is unknown."""
    from core.runtime import turn_sink as _ts
    from core.runtime.checkpoint import load_turn
    sink = _ts.get(run_id)
    if sink is None:
        # No live sink — either the Turn never existed, completed before
        # this process started, or was evicted. Surface what the DB says
        # so the client knows whether to render the closed-stream state
        # vs. error.
        t = load_turn(run_id)
        if t is None:
            raise HTTPException(404, f"no such run: {run_id}")
        if t.state.value in ("done", "failed"):
            # Emit a synthetic single-event stream so the client's
            # handler runs `done` and cleans up cleanly.
            async def _terminal():
                import json as _json
                yield f"data: {_json.dumps({'type': 'done', 'seq': 0})}\n\n"
            return StreamingResponse(
                _terminal(), media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        raise HTTPException(410, f"run {run_id} sink no longer available")
    return StreamingResponse(
        _ts.stream_from_sink(sink, since=since),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/threads/{thread_id}/active-turn")
def thread_active_turn(thread_id: str):
    """C-0: is there an in-flight Turn on this thread? Used by the
    frontend on chat mount so a page reload during a long-running cell
    can re-surface the Stop button + re-attach the live stream (C-1).

    Source of truth is the DB (Turn rows). If the live in-memory sink
    is also present, include its last_seq so the C-1 reattach knows
    where the in-memory tail starts.

    The frontend often sends `thread_id=default` (the project's default
    investigation, materialized on first message) — same resolution as
    GET /api/messages: map to the real thread id before querying.

    Returns null if no live Turn on this thread."""
    from core.graph._schema import _conn
    if thread_id == "default":
        from core.graph.threads import find_default_thread
        thread_id = find_default_thread() or "default"
    states = ("generating", "executing_tools", "summarizing")
    placeholders = ",".join("?" for _ in states)
    with _conn() as c:
        r = c.execute(
            f"SELECT run_id, state, started_at, updated_at "
            f"FROM runs WHERE thread_id = ? AND state IN ({placeholders}) "
            f"ORDER BY updated_at DESC LIMIT 1",
            (thread_id, *states),
        ).fetchone()
    if not r:
        return None
    out = dict(r)
    try:
        from core.runtime import turn_sink as _ts
        s = _ts.get(out["run_id"])
        out["sink_alive"] = s is not None and not s.closed
        out["last_seq"] = s.last_seq if s is not None else 0
    except Exception:  # noqa: BLE001
        out["sink_alive"] = False
        out["last_seq"] = 0
    return out


class ResumeRequest(BaseModel):
    user_text: str = ""
    # P1 #3 — for approval halts. 'approve' (run once), 'approve_session'
    # (run + remember for this session), 'reject' (don't run; return
    # rejection to the model).
    action: str | None = None


@app.post("/api/turns/{run_id}/resume")
async def turn_resume(run_id: str, req: ResumeRequest):
    """Resume an AWAITING_USER turn by streaming a new turn that picks up
    the user's reply (plan Go/Adjust, ask_clarification answer, future
    approval flows).

    The reply is appended as a normal user message into the prior turn's
    thread; a fresh Turn drives the loop and sees the prior tool_result
    + the new user message in history, so the model continues naturally.

    Same SSE shape as /api/chat. The frontend can re-use its existing
    chat-stream handler for the resume response."""
    from core.runtime.checkpoint import load_turn
    t = load_turn(run_id)
    if t is None:
        raise HTTPException(404, "no such run")
    if t.state.value != "awaiting_user":
        raise HTTPException(
            409,
            f"Turn is in state {t.state.value!r}; only awaiting_user can be resumed.",
        )

    focus_eid = t.focus_entity_id or WORKSPACE_ID
    thread_id = t.thread_id or "default"
    user_text = req.user_text or ""

    # #160: if this resume is the user clicking Go on a plan, transition
    # the plan from validated → executing now, and thread plan_entity_id
    # forward so the follow-up turn can mark it completed/failed on exit.
    plan_eid = t.plan_entity_id if t.pending_user_signal == "plan" else None
    if plan_eid:
        try:
            from content.bio.lifecycle.plans import set_plan_lifecycle
            set_plan_lifecycle(plan_eid, "executing")
        except Exception:  # noqa: BLE001
            pass    # never block resume on a lifecycle update

    # P1 #3 — approval resume. The held tool's result (real or rejection)
    # is written into the message log here so the new turn sees a complete
    # tool_use/tool_result pair when it loads history. The turn is then
    # streamed in retry-mode (no new user_text appended) so the model
    # picks up where it left off.
    approval_mode = (
        t.pending_user_signal == "approval"
        and t.pending_approval
        and req.action in ("approve", "approve_session", "reject")
    )
    if approval_mode:
        from content.bio.tools import execute_tool
        from core.graph.messages import append_message
        from core.runtime.approval import grant_for_session
        held = t.pending_approval
        tool_name = held.get("tool_name", "")
        tool_input = held.get("tool_input") or {}
        tool_use_id = held.get("tool_use_id", "")
        if req.action == "reject":
            result_obj = {"status": "rejected",
                          "note": "User declined to run this tool. Try a different approach "
                                  "or ask the user what to do."}
            result_str = json.dumps(result_obj)
        else:
            if req.action == "approve_session":
                grant_for_session(thread_id, tool_name)
            ctx = {"active_tools": [], "thread_id": thread_id, "focus_entity_id": focus_eid,
                   "session_id": t.session_id}
            result_str = execute_tool(tool_name, tool_input, ctx)
        # Write the now-resolved tool_result for the held tool_use.
        append_message("user",
                       [{"type": "tool_result", "tool_use_id": tool_use_id, "content": result_str}],
                       entity_id=WORKSPACE_ID, focus_entity_id=focus_eid, thread_id=thread_id)

    # C-1: spawn the new Turn as a background task (same pattern as
    # POST /api/chat) so it survives client disconnect.
    from core.runtime import turn_executor, turn_sink as _ts
    from datetime import datetime, timezone
    new_run_id = turn_executor.new_run_id()
    started_at = datetime.now(timezone.utc).isoformat()
    body_gen = stream_response(
        user_text,
        focus_entity_id=focus_eid,
        thread_id=thread_id,
        plan_entity_id=plan_eid,
        # Approval resume: don't append a new user message — the held
        # tool_result we just wrote is what advances the conversation.
        retry=approval_mode,
        run_id=new_run_id,
    )
    sink = turn_executor.start_turn(
        run_id=new_run_id,
        thread_id=thread_id,
        started_at=started_at,
        body_gen=body_gen,
    )
    return StreamingResponse(
        _ts.stream_from_sink(sink, since=0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class DeferredResultRequest(BaseModel):
    result: dict | None = None
    error:  str | None = None


@app.post("/api/turns/{run_id}/tool_result/{tool_use_id}")
async def turn_tool_result(run_id: str, tool_use_id: str, req: DeferredResultRequest):
    """P2 #4 — webhook for deferred tool results. The tool that returned
    `{deferred: true}` calls this when its real work completes. We write
    the tool_result into the message log and resume the turn via the same
    streaming mechanism as approval-resume.

    Authentication: none in v1 (single-user backend). When multi-user
    lands, sign the deferred_id and verify here."""
    from core.runtime.checkpoint import load_turn
    from core.graph.messages import append_message

    t = load_turn(run_id)
    if t is None:
        raise HTTPException(404, "no such run")
    if t.state.value != "awaiting_tool_result":
        raise HTTPException(409, f"turn is in state {t.state.value!r}, not awaiting_tool_result")
    if not t.pending_deferred or t.pending_deferred.get("tool_use_id") != tool_use_id:
        raise HTTPException(
            409,
            f"turn is awaiting a different tool_use_id "
            f"({t.pending_deferred.get('tool_use_id') if t.pending_deferred else None!r})",
        )

    focus_eid = t.focus_entity_id or WORKSPACE_ID
    thread_id = t.thread_id or "default"

    # Build the tool_result payload. Error path produces a structured error
    # the model can react to; success path passes the tool's result through.
    if req.error:
        result_obj = {"status": "error", "note": req.error,
                      "deferred_id": t.pending_deferred.get("deferred_id")}
    else:
        result_obj = req.result or {"status": "ok", "note": "(empty result)"}
    result_str = json.dumps(result_obj)

    # Write the now-resolved tool_result for the held tool_use.
    append_message("user",
                   [{"type": "tool_result", "tool_use_id": tool_use_id, "content": result_str}],
                   entity_id=WORKSPACE_ID, focus_entity_id=focus_eid, thread_id=thread_id)

    # C-1: spawn the resume Turn as a background task (same pattern as
    # POST /api/chat) so it survives client disconnect.
    from core.runtime import turn_executor, turn_sink as _ts
    from datetime import datetime, timezone
    new_run_id = turn_executor.new_run_id()
    started_at = datetime.now(timezone.utc).isoformat()
    body_gen = stream_response(
        "",
        focus_entity_id=focus_eid,
        thread_id=thread_id,
        # Retry mode: don't append a new user message — the tool_result
        # we just wrote is what advances the conversation.
        retry=True,
        run_id=new_run_id,
    )
    sink = turn_executor.start_turn(
        run_id=new_run_id,
        thread_id=thread_id,
        started_at=started_at,
        body_gen=body_gen,
    )
    return StreamingResponse(
        _ts.stream_from_sink(sink, since=0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/turns/{run_id}/cancel")
def turn_cancel(run_id: str, req: ResumeRequest):
    """Real cancellation: invoke the live CancelToken (kills any
    registered subprocesses + sets the in-loop cancel flag) AND mark
    the DB row FAILED. The order matters — we cancel the in-flight
    work BEFORE the DB write so the loop sees the cancel during its
    next poll and emits a 'cancelled' SSE event.

    Idempotent: a second cancel returns {killed: False} (the token's
    already fired). Safe on already-DONE turns — the DB write is a
    no-op because cancel_turn() checks state.

    Fallback: if the named run_id has no live token but EXACTLY ONE
    other turn is in flight, cancel it too. This catches the failure
    mode where the frontend's currentRunIdRef has gone stale (e.g.
    the agent's loop spans multiple turns and the manifest event for
    the latest one hasn't reached the client yet) — the user clearly
    wants the agent to stop NOW; refusing because of a stale id is
    worse than cancelling the obvious candidate. With >1 in flight we
    don't guess — return the active set so the client can be explicit."""
    from core.runtime import cancellation
    from core.runtime.checkpoint import cancel_turn
    reason = req.user_text.strip() or "user cancelled"

    killed = False
    fallback_run_id: str | None = None
    tok = cancellation.get(run_id)
    active = cancellation.active_run_ids()
    if tok is not None:
        killed = tok.cancel(reason=reason)
    elif len(active) == 1 and active[0] != run_id:
        fallback_run_id = active[0]
        ftok = cancellation.get(fallback_run_id)
        if ftok is not None:
            killed = ftok.cancel(reason=reason)

    ok = cancel_turn(run_id, reason=reason)
    if fallback_run_id and not ok:
        # If the requested run_id was unknown to the DB layer too,
        # still mark the actually-cancelled turn as failed.
        cancel_turn(fallback_run_id, reason=reason)
    return {"ok": ok, "killed": killed, "run_id": run_id,
            "fallback_run_id": fallback_run_id, "active": active}


@app.get("/api/admin/mcp")
def admin_mcp_status():
    """Per-server health, tool counts, last error — drawer can show
    'MCP: 2/3 servers up'."""
    from core.runtime.mcp import status
    return status()


@app.get("/api/admin/tool_stats")
def admin_tool_stats(days: int = 30):
    """Per-tool aggregates: invocation count, ok/error/rejected/deferred
    breakdown, average + max duration. Window defaults to 30 days."""
    from core.runtime.tool_telemetry import stats
    return stats(days=days)


@app.get("/api/admin/tool_invocations")
def admin_tool_invocations(limit: int = 50, tool_name: str | None = None):
    """Raw recent invocations for debugging."""
    from core.runtime.tool_telemetry import recent_invocations
    return recent_invocations(limit=limit, tool_name=tool_name)


@app.post("/api/admin/purge_orphan_fills")
def admin_purge_orphan_fills():
    """One-shot cleanup for the buggy-reaper duplication: removes user
    messages whose content is entirely orphan-fill tool_results. Safe to
    call repeatedly (no-op on a clean DB). Uses the backend's own
    connection so it doesn't violate the never-touch-live-DB rule."""
    from core.runtime.checkpoint import purge_orphan_fill_messages
    n = purge_orphan_fill_messages()
    return {"touched": n}


# ----- Skills (B2 read API) -----
# Skill catalog is registered at import time by bio/skills/__init__.py.
# The model reads it via the read_skill tool; the drawer reads it via
# these endpoints so the user can see what procedures the agent can run.

@app.get("/api/skills")
def skills_list():
    """All registered skills, name + description + small metadata.
    Bodies are excluded so the response stays cheap; fetch one via
    /api/skills/{name}."""
    from core.skills import list_skills
    return [
        {
            "name": s.name,
            "description": s.description,
            "when_to_use": s.when_to_use,
            "requires_tools": list(s.requires_tools),
            "produces": list(s.produces),
            "resource_profile": s.resource_profile,
        }
        for s in list_skills()
    ]


@app.get("/api/skills/{name}")
def skill_get(name: str):
    """Full skill including the markdown body."""
    from core.skills import get_skill
    s = get_skill(name)
    if s is None:
        raise HTTPException(404, f"skill {name!r} not registered")
    return {
        "name": s.name,
        "description": s.description,
        "when_to_use": s.when_to_use,
        "requires_tools": list(s.requires_tools),
        "produces": list(s.produces),
        "resource_profile": s.resource_profile,
        "parameter_schema": s.parameter_schema,
        "body": s.body,
    }


# ----- Memory (B3 read API) -----
# Per-project memory/ directory. Reads here; writes stay model-only via
# the write_memory tool (UI may eventually surface its own editor, but
# the source of truth is the per-project markdown files).

@app.get("/api/memory")
def memory_list():
    """Index + entry list for the current project's memory directory.
    Entries omit bodies; fetch one via /api/memory/{name}."""
    from core.memory import list_memories, read_memory_index
    return {
        "index": read_memory_index(),
        "entries": [
            {"name": e.name, "type": e.type, "description": e.description}
            for e in list_memories()
        ],
    }


@app.get("/api/memory/{name}")
def memory_get(name: str):
    """Full memory body + metadata."""
    from core.memory import read_memory
    e = read_memory(name)
    if e is None:
        raise HTTPException(404, f"memory {name!r} not found")
    return {
        "name": e.name,
        "type": e.type,
        "description": e.description,
        "body": e.body,
    }


@app.get("/api/threads/{tid}/manifest")
def thread_latest_manifest(tid: str):
    """Drawer fallback (T2.4): most-recent persisted Manifest snapshot
    for this thread (or any thread if none for this one yet). The live
    drawer subscribes to the SSE 'manifest' event during chat; this
    endpoint hydrates the initial state."""
    from core.graph.audit import latest_manifest_for_thread
    m = latest_manifest_for_thread(tid)
    if m is None:
        return {"manifest": None}
    return {"manifest": m}


@app.get("/api/manifest/preview")
def manifest_preview(focus_entity_id: str | None = None, thread_id: str | None = None):
    """Build a Manifest live for the current (focus, thread) without
    running a turn. This is what the Drawer hits whenever the user
    refocuses or switches threads — so the panel reflects what the agent
    WOULD see if they sent a message right now, not the last turn's
    snapshot.

    No persistence; the row only gets written when an actual turn runs.
    """
    from core.manifest.assembler import build_manifest
    import content.bio.cards  # noqa: F401 — ensure per-type builders register
    # Tolerate the "default" sentinel used elsewhere in the UI.
    resolved_thread = None
    if thread_id and thread_id != "default":
        resolved_thread = thread_id
    m = build_manifest(
        session_id="preview",
        turn_index=0,
        focus_entity_id=focus_entity_id,
        thread_id=resolved_thread,
    )
    return {"manifest": m.to_dict()}


@app.get("/api/files/tree")
def files_tree(include_archived: bool = False):
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


@app.get("/api/files/download")
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
    import datetime
    import content.bio  # noqa: F401 — register builders
    from content.bio.files.tree import build_files_tree, find_node, iter_files
    from core.files.materialize import _resolve_artifact_disk_path

    tree = build_files_tree(include_archived=False)
    node = find_node(tree, path)
    if node is None:
        raise HTTPException(404, f"no node at {path!r}")
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


@app.post("/api/projects/{pid}/materialize")
def project_materialize(pid: str, clean: bool = False, include_archived: bool = False):
    """Build projects/<pid>/files/ as a navigable folder tree on disk
    (files.md §8). Symlinks where supported, copies on systems that
    can't. Idempotent — running it twice converges.
    """
    from core.files.materialize import materialize_tree
    from core import projects
    out = projects.PROJECTS_DIR / pid / "files"
    summary = materialize_tree(out, include_archived=include_archived, clean=clean)
    return summary


@app.post("/api/files/promote")
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


@app.get("/api/viewers/registry")
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


@app.get("/api/viewers/for")
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

    node: dict = {}
    if entity_id:
        e = get_entity(entity_id)
        if not e:
            raise HTTPException(404, f"no entity {entity_id}")
        node = {
            "entity_id": e["id"],
            "entity_type": e["type"],
            "name": e.get("title") or "",
            "artifact_path": e.get("artifact_path"),
            "size": None,
        }
    elif path:
        from content.bio.files.tree import build_files_tree, find_node
        tree = build_files_tree(include_archived=False)
        n = find_node(tree, path)
        if n is None:
            raise HTTPException(404, f"no node at {path!r}")
        node = n
    else:
        raise HTTPException(400, "supply either entity_id or path")

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


@app.get("/api/files/content")
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


@app.get("/api/files/raw")
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


@app.post("/api/files/ai-summary")
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


@app.get("/api/health")
def health():
    return {"ok": True}
