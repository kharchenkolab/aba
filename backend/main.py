import asyncio
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Size BLAS/OMP thread pools to the CPU ALLOCATION (Slurm/cgroup/affinity), not
# the host core count — BEFORE numpy/torch import here or in any kernel/subprocess
# we later spawn. On a node allocated few CPUs out of many (e.g. an OnDemand Slurm
# node), OpenBLAS would otherwise spawn one thread per host core and hit the
# per-user process limit (pthread EAGAIN), killing run_r/run_python. See
# core/exec/cpu.py. setdefault, so a launch-script/operator value still wins.
from core.exec.cpu import pin_blas_threads as _pin_blas_threads
_pin_blas_threads()

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import StreamingResponse, FileResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.config import ARTIFACTS_DIR, DATA_DIR
from content.bio.graph.result_members import update_result_member
from core.graph._schema import init_db, gen_entity_id, WORKSPACE_ID
from core.graph.edges import add_edge, remove_edge, edges_from, edges_to
from core.graph.entities import list_entities, get_entity, create_entity, update_entity, archive_entity, restore_entity, delete_entity_hard
from core.web.deps import require_project
from core.graph.messages import get_messages, clear_messages
from guide import stream_response
# Wave 2 A.3: register the bio content pack BEFORE any request handler
# can fire. guide.py reads `active_pack()` at the top of stream_response,
# so the pack must be live by the time the first chat request lands.
# register_hooks() triggers the per-handler imports that used to live
# at the top of guide.py as noqa: F401.
from content.bio import BIO_PACK as _BIO_PACK
from core.runtime.content_pack import set_active_pack as _set_active_pack
_set_active_pack(_BIO_PACK)
_BIO_PACK.register_hooks()

from content.bio.lifecycle.adaptive import run_probe
from content.bio.graph.figure_history import figure_history
from core.graph.jobs import list_jobs, get_job
from core.jobs.runner import start_worker, cancel_job


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def _value_error_to_422(_request, exc: ValueError):
    """WU-2: Phase 4.5 entity_types validators raise ValueError on a
    schema/edge/transition violation. Convert to HTTP 422 at the
    boundary so the frontend sees a structured 'bad request' error
    rather than an opaque 500. Other ValueErrors fall through the same
    handler — semantically still 'bad input', so 422 is appropriate."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)},
    )


# Project-context pinning middleware (#17) — moved to core/web/middleware.py
# (Item 2A.2). Pins each request's project via a contextvar bind so concurrent
# requests for different projects don't race the process-global DB_PATH.
from core.web.middleware import ProjectPinMiddleware
app.add_middleware(ProjectPinMiddleware)

import threading

# Per-project artifacts (post 2026-05-31 reorg) live under
# projects/<pid>/artifacts/<name>. The URL scheme is /artifacts/<pid>/<name> —
# served by the route handler below. Legacy single-dir mount removed.
# _artifact_url_to_path moved to core/web/artifacts.py (Item 2A.1) so content code
# imports it from core, not up from main; re-exported here for back-compat
# (tests/regtest still do `from main import _artifact_url_to_path`).
from core.web.artifacts import _artifact_url_to_path  # noqa: F401


@app.get("/artifacts/{pid}/{name}")
def serve_artifact(pid: str, name: str):
    from core.config import project_artifacts_dir
    if "/" in name or ".." in name or "/" in pid or ".." in pid:
        raise HTTPException(400, "invalid artifact path")
    f = project_artifacts_dir(pid) / name
    if not f.is_file():
        raise HTTPException(404, f"artifact {pid}/{name} not found")
    return FileResponse(str(f))


# arch3.md Phase 8.A: mount bio's HTTP routes (currently /api/claims/*;
# more clusters to follow). The router pattern means each Phase 8
# extraction is local to content/bio/web/routes.py — main.py mounts
# once and doesn't grow.
from content.bio.web import router as _bio_router
app.include_router(_bio_router)

# Domain-neutral platform routers extracted from main.py (Item 2A.3) — see
# core/web/routers/. Reasoning-plane entries (chat/resume/tool_result) stay
# inline below since they import guide (core/ must not).
from core.web.routers import admin as _admin_routes
app.include_router(_admin_routes.router)
from core.web.routers import jobs as _jobs_routes
app.include_router(_jobs_routes.router)
from core.web.routers import settings as _settings_routes
app.include_router(_settings_routes.router)
from core.web.routers import memory as _memory_routes
app.include_router(_memory_routes.router)
from core.web.routers import threads as _threads_routes
app.include_router(_threads_routes.router)
from core.web.routers import projects as _projects_routes
app.include_router(_projects_routes.router)
from core.web.routers import turns as _turns_routes
app.include_router(_turns_routes.router)
from core.web.routers import misc as _misc_routes
app.include_router(_misc_routes.router)


# Startup/shutdown lifecycle → lifespan.py (Item 2A.2). Composition-root
# level (wires content: R base, display backfill, aba_core MCP server), so
# it lives beside main.py, not under core/.
from lifespan import register_lifecycle as _register_lifecycle
_register_lifecycle(app)


# ---------- Projects ----------

# Project-lifecycle + /api/env routes → core/web/routers/projects.py (Item 2A.3).


# ---------- Bundle ----------

# bundle_state → core/web/routers/misc.py (Item 2A.3).


# ---------- Entities ----------

# entity_types_catalog → core/web/routers/misc.py (Item 2A.3).


@app.get("/api/entities")
def entities_list(
    q: str | None = None,
    type: str | None = None,
    include_archived: bool = True,
    limit: int | None = None,
    offset: int = 0,
    project_id: str | None = None,
):
    _require_project_context(project_id)
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
def entities_get(entity_id: str, _pid: str = Depends(require_project)):
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
def entities_patch(entity_id: str, req: EntityPatch, _pid: str = Depends(require_project)):
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


def _result_cascade_set(result_id: str) -> set[str]:
    """Containment set for `cascade=members` on a Result: every figure/
    table/cell member referenced from metadata.members, plus each
    member's full revision chain (active + superseded). The Result id
    itself is NOT included — it's deleted separately at the end.

    Why include superseded revisions: when the user deletes a Result,
    they expect the whole history to go with it (the superseded
    revisions are figure entities created via make_revision that
    aren't referenced anywhere visible; leaving them as orphans
    would just look like a memory leak)."""
    from content.bio.graph.figure_history import figure_history
    out: set[str] = set()
    r = get_entity(result_id)
    if not r:
        return out
    members = (r.get("metadata") or {}).get("members") or []
    member_ids = [m.get("ref") for m in members if isinstance(m, dict) and m.get("ref")]
    for mid in member_ids:
        m = get_entity(mid)
        if not m:
            continue
        out.add(mid)
        # Expand revision chains for figure/table members. Cells don't
        # currently form revision chains via wasRevisionOf, but
        # figure_history is safe on any type — it'll just return [m].
        if m.get("type") in ("figure", "table"):
            try:
                chain = figure_history(mid, include_superseded=True)
                for e in chain:
                    if e and e.get("id"):
                        out.add(e["id"])
            except Exception:  # noqa: BLE001 — chain walk is best-effort
                pass
    return out


@app.delete("/api/entities/{entity_id}")
def entities_delete(entity_id: str, hard: bool = False,
                    cascade: str | None = None,
                    _pid: str = Depends(require_project)):
    """Soft-delete (default): mark status='archived'. Workspace cannot be deleted.

    With ?hard=true: hard-delete after refusing if other (non-archived) entities
    reference this one via entity_edges. For dataset entities whose artifact is
    a directory under the project's data dir, the directory is removed too.

    With ?hard=true&cascade=members on a RESULT: includes/supports/
    wasDerivedFrom edges from the Result to its figure/table/cell members
    are treated as containment, not as live references. The cascade
    expands to each member's full revision chain (active + superseded —
    walked via figure_history). A member that is ALSO referenced from
    outside the cascade set (e.g. included in another Result, cited by
    a Claim) is preserved and its inbound edge from the Result is
    silently removed; the response's `skipped[]` lists such members so
    the UI can surface them.

    Returns {"ok": true, "deleted": <entity>, "cascade_deleted": [...],
    "skipped": [...]} on hard, the archived entity on soft."""
    if entity_id == WORKSPACE_ID:
        raise HTTPException(400, "workspace cannot be deleted")
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"Entity {entity_id} not found")
    if not hard:
        updated = archive_entity(entity_id)
        if not updated:
            raise HTTPException(404, f"Entity {entity_id} not found")
        return updated

    # cascade=members on a Result: figure out the containment set first
    # so we can (a) ignore intra-cascade edges during the blocker check
    # and (b) hard-delete those members at the end. Builds the set
    # transitively: Result → members → each member's revision chain.
    cascade_set: set[str] = set()
    if cascade == "members" and ent.get("type") == "result":
        cascade_set = _result_cascade_set(entity_id)

    # Hard-delete: refuse if any non-archived, non-cascade-set entity
    # points at us (inbound), or if we point at any non-archived,
    # non-cascade-set entity (outbound). Edges to archived entities are
    # fine — they're already gone from the user's view. Edges into the
    # cascade set (members + revision chains) are fine — they'll be
    # deleted by the cascade.
    blockers: list[dict] = []
    for e in edges_to(entity_id) + edges_from(entity_id):
        other_id = e["source_id"] if e["target_id"] == entity_id else e["target_id"]
        if other_id == entity_id:
            continue
        if other_id in cascade_set:
            continue
        other = get_entity(other_id)
        if other and other.get("status") != "archived":
            blockers.append({"id": other_id, "type": other.get("type"),
                             "title": other.get("title"), "rel_type": e["rel_type"]})
    if blockers:
        raise HTTPException(409, {
            "error": "entity has live references; archive instead, or remove the references first",
            "references": blockers[:20],
        })

    # Delete the on-disk artifact for dataset-shaped entities. Only remove paths
    # under the project's data dir — never traverse outside it.
    from core.config import current_project_id, project_data_dir
    data_root = project_data_dir(current_project_id()).resolve()
    ap = ent.get("artifact_path")
    if ap and ent.get("type") == "dataset":
        try:
            path = Path(ap).resolve()
            if data_root in path.parents or path == data_root:
                if path != data_root and path.exists():
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
        except OSError:
            pass

    # Cascade delete: walk the containment set, skip any member that has
    # live references OUTSIDE the cascade scope (e.g. it's also a member
    # of another Result, or cited by a Claim). For skipped members we
    # detach the Result→member edges so the user-facing Result row
    # cleanly disappears without orphaning the member's graph context.
    cascade_deleted: list[dict] = []
    skipped: list[dict] = []
    if cascade_set:
        # Topological-ish order: leaves of the revision chain first so
        # wasRevisionOf edges resolve cleanly. Easiest proxy: delete in
        # any order — delete_entity_hard tolerates missing references
        # since edges are cascade-deleted by FK on the entity rows.
        # Only INBOUND, dependency-forming edges count as outside
        # references when deciding whether to preserve a cascade member.
        # Provenance edges (wasGeneratedBy from analyses/runs) are
        # bookkeeping — they don't make a Run "depend on" a figure in
        # the user's sense. Without this filter, every harvested figure
        # gets kept just because its parent Analysis exists.
        _DEP_RELS = {"includes", "supports", "wasDerivedFrom", "wasRevisionOf"}
        for member_id in list(cascade_set):
            m = get_entity(member_id)
            if not m:
                continue
            outside = []
            for e in edges_to(member_id):
                if e["rel_type"] not in _DEP_RELS:
                    continue
                src = e["source_id"]
                if src in cascade_set or src == entity_id:
                    continue
                ent_other = get_entity(src)
                if ent_other and ent_other.get("status") != "archived":
                    outside.append({"id": src, "type": ent_other.get("type"),
                                    "title": ent_other.get("title"),
                                    "rel_type": e["rel_type"]})
            if outside:
                # Keep this member; just detach the Result→member edges
                # so the visible Result delete is clean. wasRevisionOf
                # edges stay (member still has its chain).
                from core.graph.edges import remove_edge
                for rel in ("includes", "supports", "wasDerivedFrom"):
                    try: remove_edge(entity_id, member_id, rel)
                    except Exception: pass  # noqa: BLE001
                skipped.append({"id": member_id, "type": m.get("type"),
                                "title": m.get("title"),
                                "kept_because": outside[:5]})
                continue
            if delete_entity_hard(member_id):
                cascade_deleted.append({"id": member_id, "type": m.get("type"),
                                        "title": m.get("title")})

    if not delete_entity_hard(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    out: dict = {"ok": True, "deleted": ent}
    if cascade_set:
        out["cascade_deleted"] = cascade_deleted
        out["skipped"] = skipped
    return out


@app.post("/api/entities/{entity_id}/restore")
def entities_restore(entity_id: str, _pid: str = Depends(require_project)):
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
def entities_clear_messages(entity_id: str, _pid: str = Depends(require_project)):
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
    # Multi-member Result viewport hint (2026-06-13): when a Result has
    # more than one panel, the frontend names the member the user has in
    # view at send time. None when there's only one major member (the
    # single-panel case behaves as before) or the focused entity isn't a
    # Result. The Result focus card marks the named member so the agent
    # anchors on it when the user says "this plot".
    focus_member_id: str | None = None
    # The thread (line of inquiry) this turn belongs to. "default" = the
    # implicit default thread (small projects never name one).
    thread_id: str = "default"
    # The project this chat belongs to. Per-request so the backend's global
    # "current project" state can't silently misroute requests after a server
    # bounce / multi-tab / side-script set_current() (PK 2026-06-02). The
    # handler set_current()s on entry if it differs.
    project_id: str | None = None
    # Spatial reference (Phase 25): base64 PNG of the figure with the user's
    # annotation composited on, plus a short note describing the gesture.
    annotation_image: str | None = None
    annotation_note: str | None = None
    # Chat attachments (the composer paperclip / clipboard paste). Each is a ref
    # from POST /api/attach: {name, path, kind, is_image, size_bytes, url}.
    # Persisted as a UI chip block + injected as an ephemeral agent context note
    # (+ vision blocks for images) for this turn. See core.runtime.attachments.
    attachments: list[dict] | None = None
    # Regenerate the last turn's reply without appending a new user message
    # (used by the message-level retry after a transient API failure).
    retry: bool = False
    # Per-turn primary-spec override. Highest precedence in the lean-vs-
    # full selection chain:
    #   request.spec → thread.metadata.spec → ABA_PRIMARY_SPEC env →
    #   "guide" default
    # Used by the new-chat backend selector when starting a fresh
    # conversation. None / empty → fall through.
    spec: str | None = None


# _require_project_context moved to core.web.deps.require_project_context (Item 2A.3)
# so router modules share it; aliased here for main.py's own body-pinned handlers.
from core.web.deps import require_project_context as _require_project_context  # noqa: F401


@app.post("/api/chat")
async def chat(req: ChatRequest):
    # #18 — chat's project_id rides in the BODY, invisible to the ASGI pin
    # middleware (#17), so we bind it explicitly here. Binding the RESOLVED pid
    # to the contextvar for the whole setup makes get_entity() and the
    # start_turn() project capture immune to a concurrent request mutating the
    # process-global — even if an `await` is later introduced into this block
    # (today it's synchronous, so this is defense-in-depth). The spawned turn
    # task inherits the bound context and re-binds the same pid in _drain (#15).
    from core import projects as _projects
    pid = _require_project_context(req.project_id)
    with _projects.bind(pid):
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
            focus_member_id=req.focus_member_id,
            thread_id=req.thread_id,
            annotation_image=req.annotation_image,
            annotation_note=req.annotation_note,
            attachments=req.attachments,
            retry=req.retry,
            run_id=run_id,
            spec_override=req.spec,
        )
        sink = turn_executor.start_turn(
            run_id=run_id,
            thread_id=req.thread_id,
            started_at=started_at,
            body_gen=body_gen,
        )
    # The SSE generator reads only the in-memory sink + turn_events JSONL (never
    # the project DB), so it runs correctly outside the bound context.
    return StreamingResponse(
        _ts.stream_from_sink(sink, since=0),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/turns/{run_id}/tool_stream/{tool_use_id}")
def turns_tool_stream(run_id: str, tool_use_id: str):
    """Replay snapshot of a live-tailed run_python / run_r output (#334 Phase 2).

    Frontend calls this on initial chat load + on SSE reconnect/tab-focus
    for any tool_start whose tool_use_id has no matching tool_result yet
    (or that just landed and the user reopened the tab while the buffer is
    still warm). Returns 404 once the buffer is GC'd."""
    from core.runtime import tool_stream_buffer as _tsb
    snap = _tsb.get(run_id, tool_use_id)
    if snap is None:
        raise HTTPException(404, "no live-stream buffer for that tool_use_id")
    return snap


# Phase 8.C: /api/runs/{rid}/refresh-manifest moved to bio.


# Phase 8.B-2: /api/results/{rid}/regenerate-interpretation moved to bio.


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
def messages_list(thread_id: str | None = None, project_id: str | None = None):
    """The project's conversation. `thread_id` scopes to one thread
    ("default" = the default thread, materialized or not); omitted = all.

    `project_id` pins the project per-request — without it, a first-load
    race condition shows an empty chat: the frontend fires this in parallel
    with /api/projects/{pid}/open, and if it lands first the backend reads
    from the previous project's (or scratch's) DB and returns []. The
    second refresh works because by then the project switch has happened
    (PK 2026-06-03)."""
    _require_project_context(project_id)
    if thread_id == "default":
        from core.graph.threads import find_default_thread
        thread_id = find_default_thread() or "default"   # real id if materialized
    return get_messages(WORKSPACE_ID, thread_id=thread_id)


# ---------- Threads (v3 lines of inquiry) ----------

# Thread + open-question routes → core/web/routers/threads.py (Item 2A.3).


# ---------- Proactive proposals (Phase D) ----------

# Phase 8.E: /api/threads/{tid}/proposals|evaluate|orient + EvaluateRequest
# moved to content/bio/web/routes.py.


# Phase 8.D: /api/proposals/* (accept, dismiss, undo) moved to bio.


# Phase 8.C: /api/runs/* (cancel, pin-output, register-dataset, tree, file)
# + PinOutputRequest + RegisterDatasetRequest + _run_or_404 helper + /api/datasets/
# {did}/tree moved to content/bio/web/routes.py.

# Phase 8.B-1: /api/results/* CRUD (create + members + reorder) +
# helpers (_result_or_404, MemberRequest, CreateResultRequest,
# ReorderRequest) moved to content/bio/web/routes.py.


# Phase 8.D: /api/entities/{id}/suggest-interpretation + _llm_figure_caption
# moved to content/bio/web/routes.py.


# Phase 8.B-2: /api/messages/pin + PinMessageRequest moved to bio.


@app.get("/api/search")
def search_endpoint(q: str = "", limit: int = 25):
    """Faceted search across entities + chat snippets (M9 fallback recovery)."""
    from content.bio.graph.search import search as _search
    return _search(q, limit=limit)


class ClientContextIn(BaseModel):
    context: dict = {}


@app.post("/api/feedback/client-context")
def feedback_client_context(payload: ClientContextIn):
    """Stash a browser-side context snapshot (recent console errors, route, UI
    state) so Guide can read it via read_client_context when filing a bug report
    — Guide can't see the browser otherwise. Captured on bug-button click; not
    sent anywhere until the user files a report. No project context needed."""
    from content.bio.tools.feedback import stash_client_context
    stash_client_context(payload.context)
    return {"ok": True}


# arch3.md Phase 8.A: /api/claims/* (12 endpoints) + claim helpers +
# Pydantic models + the CONFIDENCE constant moved to
# content/bio/web/routes.py. main.py mounts the bio router (see the
# include_router call near the end of file). Subsequent Phase 8 commits
# extract more entity-aware clusters into the same router.
#
# Block 1B follow-up: the small helpers (`_now`, `_resolve_thread`)
# that used to live here for the in-main handlers are gone — the bio
# router carries its own copies (2-line stamps not worth a core module).


# Phase 8.B-2: /api/entities/{figure_id}/promote-to-result + PromoteFigureRequest /
# PromoteResultsRequest / PromoteFindingsRequest moved to bio.


# Phase 8.D: advisor-notes endpoints + /api/entities/{id}/advise
# + /api/context-suggestions/* + AdvisorNoteStatusRequest +
# SuggestionAction moved to content/bio/web/routes.py.


# Phase 8.B-2: /api/findings/*, /api/narratives, + their Pydantic models
# (NarrativeRequest, FindingResultRequest, DraftFindingRequest,
# CreateFindingRequest, FindingFieldsRequest) moved to bio.


@app.get("/api/entities/{entity_id}/edges")
def entities_edges(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return {
        "outgoing": edges_from(entity_id),
        "incoming": edges_to(entity_id),
    }


# Phase 8.E: /api/entities/{id}/history, /provenance, /create-scenario
# (with ScenarioRequest) moved to content/bio/web/routes.py.


# ---------- Chat attachments (composer paperclip) ----------


@app.post("/api/attach")
async def attach(file: UploadFile = File(...), thread_id: str = Form("default"),
                 _pid: str = Depends(require_project)):
    """Stash a chat attachment in the thread's scratch area (NOT a dataset entity
    — the agent registers it only if the user asks). Returns the ref the chat
    turn carries + a serve url for the chip/thumbnail."""
    if not file.filename:
        raise HTTPException(400, "filename missing")
    from core.config import current_project_id
    from core.runtime.attachments import save_attachment
    return save_attachment(current_project_id(), thread_id, file.filename, file.file)


@app.get("/api/attachments/{thread_id}/{name}")
def serve_attachment(thread_id: str, name: str, _pid: str = Depends(require_project)):
    """Serve a stashed chat attachment (project-scoped, path-traversal guarded)."""
    from core.config import current_project_id
    from core.runtime.attachments import attachments_root
    root = attachments_root(current_project_id(), thread_id).resolve()
    f = (root / Path(name).name).resolve()
    if not str(f).startswith(str(root) + os.sep) or not f.is_file():
        raise HTTPException(404, "attachment not found")
    return FileResponse(str(f))


# ---------- Upload ----------


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), _pid: str = Depends(require_project)):
    """Drop an uploaded file into the active project's data dir + register as a
    'dataset' entity."""
    if not file.filename:
        raise HTTPException(400, "filename missing")
    safe_name = Path(file.filename).name
    from core.config import current_project_id, project_data_dir
    from core.data.paths import unique_path
    dest = unique_path(project_data_dir(current_project_id()) / safe_name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    size = dest.stat().st_size
    from core.graph.derivation import imported, human_actor
    eid = create_entity(
        entity_type="dataset",
        title=dest.name,
        artifact_path=str(dest),
        derivation=imported(file.filename or dest.name),   # Phase 2B
        actor=human_actor(),
        metadata={"size_bytes": size, "original_name": file.filename},
    )
    # The "data added" reaction (Guide orientation) is driven by the frontend via
    # POST /api/threads/:id/orient after the upload lands — one synchronous source,
    # so the client can reload the chat + chips and there's no duplicate-post race.
    return get_entity(eid)


# Phase 8.C: /api/datasets POST + /api/upload-folder + dataset helpers
# (_dataset_bytes_and_count, _refresh_dataset_layout_hint, _unique_dir_path)
# moved to content/bio/web/routes.py.
#
# Block 1B follow-up: dead copies of those helpers + _unique_path
# deleted from this file. /api/upload + /api/upload-url use the shared
# core.data.paths.unique_path.


# Phase 8.E: /api/results/external + /api/results/{rid}/upload-evidence
# moved to content/bio/web/routes.py.

# Phase 8.B-2: /api/entities/{id}/pin + /api/entities/{id}/unpin moved to bio.


class URLUploadRequest(BaseModel):
    url: str
    title: str | None = None


@app.post("/api/upload-url")
async def upload_url(req: URLUploadRequest, _pid: str = Depends(require_project)):
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
    from core.data.paths import unique_path
    dest = unique_path(project_data_dir(current_project_id()) / name)

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

    from core.graph.derivation import imported, human_actor
    eid = create_entity(
        entity_type="dataset",
        title=req.title or dest.name,
        artifact_path=str(dest),
        derivation=imported(req.url),   # Phase 2B
        actor=human_actor(),
        metadata={
            "size_bytes": dest.stat().st_size,
            "source_url": req.url,
            "original_name": name,
        },
    )
    return get_entity(eid)


# ---------- Jobs (Phase 17 + Phase A) ----------

# Jobs routes → core/web/routers/jobs.py (Item 2A.3).


# Phase 8.E: /api/home-summary + /api/sample-project moved to
# content/bio/web/routes.py.


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


# events_list → core/web/routers/misc.py (Item 2A.3).


# notifications_stream → core/web/routers/misc.py (Item 2A.3).


# Turn read/stream routes → core/web/routers/turns.py (Item 2A.3).
# (chat/resume/tool_result stay below — they call guide; active-turn stays — it calls _conn.)


@app.get("/api/threads/{thread_id}/active-turn")
def thread_active_turn(thread_id: str, project_id: str | None = None):
    """C-0: is there an in-flight Turn on this thread? Used by the
    frontend on chat mount so a page reload during a long-running cell
    can re-surface the Stop button + re-attach the live stream (C-1).

    Source of truth is the DB (Turn rows). If the live in-memory sink
    is also present, include its last_seq so the C-1 reattach knows
    where the in-memory tail starts.

    The frontend often sends `thread_id=default` (the project's default
    investigation, materialized on first message) — same resolution as
    GET /api/messages: map to the real thread id before querying.

    `project_id` pins the project per-request — without it the read
    races with /api/projects/{pid}/open and can hit the wrong project's
    Turn rows (similar to the /api/messages race fixed in cb8658e).

    Returns null if no live Turn on this thread."""
    _require_project_context(project_id)
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
    # See ChatRequest.project_id — same per-request pinning.
    project_id: str | None = None


@app.post("/api/turns/{run_id}/resume")
async def turn_resume(run_id: str, req: ResumeRequest):
    _require_project_context(req.project_id)
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


# Admin/diagnostics routes → core/web/routers/admin.py (Item 2A.3).


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
            "layer": s.layer,              # provenance: which scope contributed it
            "visibility": s.visibility,    # 'always' (core tier) | 'local'
            "domain": s.domain,
            "kind": s.kind,                # 'recipe' (executable) | 'knowhow' (advice)
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

# Memory routes → core/web/routers/memory.py (Item 2A.3).


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
def files_tree(include_archived: bool = False, project_id: str | None = None):
    _require_project_context(project_id)
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

    # Single file → stream it directly. (Earlier behavior zipped a one-file
    # download with an empty arcname → corrupt .zip. PK 2026-06-02: tried to
    # download seurat_scrna_v2_draft.md from the Files tab, got an invalid
    # zip back.) Real on-disk files use FileResponse so the browser gets the
    # right MIME + filename; synthesized text nodes (READMEs, claim .md
    # bodies) stream the text body inline.
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


@app.post("/api/skills/reload")
def skills_reload():
    """Re-resolve the bundle scope chain and re-project its skills into the live
    catalog (system + installation + lab + user). Lets a `git pull` in any
    scope's bundle (or an edited vendor skill) take effect without a backend
    bounce.

    Why an explicit endpoint instead of relying on uvicorn's --reload watcher:
    the institution/lab/user bundles live outside the source tree (or behind
    --reload-exclude), so edits there don't bounce uvicorn — but that also means
    new content doesn't propagate. This endpoint is the manual refresh seam.

    Response includes per-scope counts so operators can confirm the scope they
    expected to load actually loaded."""
    from core.skills.loader import _REGISTRY, list_skills
    from core.bundle.active import reload_bundle
    from content.bio.skills import register_from_bundle
    before = len(_REGISTRY)
    reload_bundle()                       # re-resolve scope chain + re-compose
    by_scope = register_from_bundle(clear=True)
    sk = list_skills()
    return {
        "status": "ok",
        "before": before,
        "after": len(sk),
        "by_scope": by_scope,             # {scope_name: skill count}
        "always": sum(1 for s in sk if s.visibility == "always"),
        "local":  sum(1 for s in sk if s.visibility != "always"),
    }


@app.post("/api/projects/{pid}/materialize")
def project_materialize(pid: str, clean: bool = False, include_archived: bool = False):
    """Build projects/<pid>/files/ as a navigable folder tree on disk
    (files.md §8). Symlinks where supported, copies on systems that
    can't. Idempotent — running it twice converges.
    """
    from core.files.materialize import materialize_tree
    from content.bio.files.tree import build_files_tree
    from core import projects
    out = projects.PROJECTS_DIR / pid / "files"
    tree = build_files_tree(include_archived=include_archived)
    summary = materialize_tree(tree, out, clean=clean)
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
            "name": e.get("title") or "",
            "artifact_path": e.get("artifact_path"),
            "size": None,
        }
    if path:
        # Tolerant resolve: exact tree path, else a basename / path-suffix match
        # (callers — incl. the agent via open_viewer — rarely know the full path).
        from content.bio.files.tree import build_files_tree, find_file_node, list_file_matches
        tree = build_files_tree(include_archived=False)
        n = find_file_node(tree, path)
        if n is None:
            cands = list_file_matches(tree, path)
            hint = f" Did you mean: {', '.join(cands)}?" if cands else ""
            raise HTTPException(404, f"no file matching {path!r} in this project.{hint}")
        return n
    raise HTTPException(400, "supply either entity_id or path")


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


@app.post("/api/viewers/launch")
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
    from core.config import current_project_id

    node = _resolve_files_node(body.entity_id, body.path)
    ext = [v for v in viewers_for(node) if v.mode == "external" and v.open_external]
    v = (next((x for x in ext if x.id == body.viewer_id), None) if body.viewer_id
         else (ext[0] if ext else None))
    if v is None:
        raise HTTPException(404, "no external viewer applies to this file")
    pid = current_project_id()

    def runner(set_phase):
        set_phase("Preparing the dataset…")
        return launch_viewer(v.open_external, node, {
            "entity_id": node.get("entity_id"), "path": body.path,
            "project_id": pid, "set_phase": set_phase,
        })
    job_id = prepare.start(runner, label=v.label or v.id)
    return {"job_id": job_id, "label": v.label or v.id}


@app.get("/api/viewers/launch/status")
def viewers_launch_status(job: str):
    """Poll a prepare job started by /api/viewers/launch."""
    from core.viewers import prepare
    s = prepare.status(job)
    if s is None:
        raise HTTPException(404, "no such prepare job")
    return s


@app.get("/api/viewers/download")
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
    from core.config import current_project_id

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


@app.get("/viewer-launch")
def viewer_launch_page():
    """The ABA-owned loading tab: starts + polls a prepare job and redirects to
    the viewer when ready (see core/viewers/launch_page). Reuses the SPA's CSS;
    no-store so it always references the current build's stylesheet."""
    from core.viewers.launch_page import render
    dist = Path(os.environ.get("ABA_FRONTEND_DIST")
                or (Path(__file__).resolve().parent.parent / "frontend" / "dist"))
    return HTMLResponse(render(dist), headers={"Cache-Control": "no-store, must-revalidate"})


# ---- pagoda3 (external viewer) co-hosting — viewers.md §3, misc/pagoda3_integration.md ----
# Serve pagoda3's static bundle + its data stores under ABA's OWN origin so the
# viewer inherits ABA's trust model (no CORS; session/localStorage work) and its
# SharedArrayBuffer workers get the cross-origin isolation headers they need.
# Registered here (before the SPA catch-all near end of file) so /pagoda3/* and
# /pagoda3-store/* match these, not the react-router HTML fallback.
_PAGODA3_DIST = Path(os.environ.get("ABA_PAGODA3_DIST")
                     or (Path.home() / "pagoda" / "pagoda3" / "web" / "dist"))


class _IsolatedStatic(StaticFiles):
    """StaticFiles that stamps cross-origin-isolation headers on every
    response — pagoda3's compute workers use SharedArrayBuffer, which
    requires COOP: same-origin + COEP: require-corp on the document.
    (If isolation ever misbehaves, pagoda3's ?noiso=1 falls back to the
    main thread.)"""
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        resp.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        # index.html has a stable URL but its content (hashed asset refs) changes
        # every build — never let the browser heuristically cache it, or it keeps
        # loading dead asset hashes → blank. Hashed assets stay cacheable.
        if (resp.headers.get("content-type") or "").startswith("text/html"):
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp


if (_PAGODA3_DIST / "index.html").is_file():
    app.mount("/pagoda3", _IsolatedStatic(directory=str(_PAGODA3_DIST), html=True), name="pagoda3")


@app.get("/pagoda3-store/{pid}/{relpath:path}")
def pagoda3_store(pid: str, relpath: str):
    """Serve one file from a project's pagoda3 data store (a `.lstar.zarr`
    tree) over HTTP Range. The path is containment-checked to the project's
    pagoda3/ dir; dotfiles (.zmetadata/.zarray/.zgroup/.zattrs) ARE served —
    they're the store's own metadata. Same-origin as the bundle so it loads
    under COEP. Range → 206 is handled by Starlette's FileResponse."""
    from core.viewers.store_serve import resolve_within
    from core.config import project_root
    base = project_root(pid) / "pagoda3"
    try:
        f = resolve_within(base, relpath)
    except ValueError:
        raise HTTPException(403, "path escapes store root")
    if not f.is_file():
        raise HTTPException(404, f"no store file {relpath!r}")
    resp = FileResponse(str(f))
    resp.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    # A store's URL is stable (source-path hash) but its CONTENT changes when it's
    # re-derived (version bump / prep). Without revalidation the browser mixes a
    # stale .zmetadata with fresh chunks → garbage ("stars"). no-cache = keep it
    # but revalidate every read (FileResponse's etag/mtime → cheap 304s when
    # unchanged, fresh bytes after a re-derive).
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---- pagoda3 copilot proxy: ABA lends its Anthropic credential ----
# pagoda3's in-viewer copilot posts to `${proxyBase}/agent/stream` (+ /health).
# We point it at /pagoda3-api (via localStorage p3-agent-proxy at launch) and
# proxy to Anthropic using ABA's OWN credential. ABA is the SOLE token renewer
# (core.llm._oauth_bearer, locked) — pagoda3 never touches ~/.aba/oauth.json,
# so it can't trigger an aberrant refresh. Mirrors pagoda3/server/proxy.mjs.
from fastapi.responses import JSONResponse as _JSONResponse  # noqa: E402


@app.get("/pagoda3-api/health")
def pagoda3_api_health(provider: str | None = None):
    from core.llm import anthropic_auth
    if provider == "openai":
        return _JSONResponse({"ok": False, "mode": "openai",
                              "error": "ABA has no local model backend"}, status_code=503)
    try:
        a = anthropic_auth()
        return {"ok": True, "mode": a["mode"], "expires_in": a["expires_in"]}
    except Exception as e:  # noqa: BLE001
        return _JSONResponse({"ok": False, "error": str(e)}, status_code=503)


@app.post("/pagoda3-api/agent/stream")
async def pagoda3_api_stream(payload: dict):
    """Relay pagoda3's copilot turn to Anthropic with ABA's credential, streaming
    the SSE back. ABA-managed token (single renewer); the viewer just relays."""
    import httpx
    from core.llm import anthropic_auth
    from core.viewers.llm_proxy import build_messages_request, anthropic_headers, ANTHROPIC_URL

    if payload.get("provider") == "openai":
        return _JSONResponse({"error": "ABA proxies Anthropic only"}, status_code=501)
    try:
        auth = anthropic_auth()
    except Exception as e:  # noqa: BLE001
        return _JSONResponse({"error": str(e)}, status_code=503)

    out = build_messages_request(payload, auth["mode"])
    headers = anthropic_headers(auth["mode"], auth["token"])

    client = httpx.AsyncClient(timeout=None)
    req = client.build_request("POST", ANTHROPIC_URL, headers=headers, json=out)
    up = await client.send(req, stream=True)
    if up.status_code >= 400:                       # relay the upstream error verbatim
        body = await up.aread()
        await up.aclose(); await client.aclose()
        return Response(content=body, status_code=up.status_code,
                        media_type=up.headers.get("content-type", "application/json"))

    async def gen():
        try:
            async for chunk in up.aiter_bytes():
                yield chunk
        finally:
            await up.aclose(); await client.aclose()

    return StreamingResponse(gen(), status_code=up.status_code, media_type="text/event-stream")


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


# health → core/web/routers/misc.py (Item 2A.3).


# ── Serve the built frontend (production single-server mode) ─────────────────
# In development the React app is served by Vite on :5173, which proxies
# /api and /artifacts here (see frontend/vite.config.ts). An installed
# deploy has no Vite — uvicorn is the only server, so it must serve the
# compiled SPA from frontend/dist. Guarded on the build existing, so this
# is a no-op in dev and only engages once `npm run build` has produced a
# dist/. Registered last, so it never shadows the /api or /artifacts routes
# above (Starlette matches routes in registration order).
_FRONTEND_DIST = Path(os.environ.get("ABA_FRONTEND_DIST")
                      or (Path(__file__).resolve().parent.parent / "frontend" / "dist"))
if (_FRONTEND_DIST / "index.html").is_file():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

    # index.html is the SPA bootstrap — it references hashed assets by
    # name, so the *contents* of index.html change every build, but the
    # *URL* doesn't. Without an explicit no-store, browsers cache it
    # indefinitely and keep loading the OLD bundle hash on the next
    # build, even when the server has the new one. Hashed assets under
    # /assets/* don't have this problem (their URLs change with content)
    # so they stay aggressively cacheable.
    _INDEX_NO_CACHE = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma":        "no-cache",
    }

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        """Serve a real file from dist/ if it exists, else the SPA shell.

        The shell fallback is what makes client-side routing work: a deep
        link like /p/X/runs/e/Y has no file on disk, so we return
        index.html and let react-router resolve the path in the browser.
        """
        # A miss on an API/artifact/viewer path must 404, not fall through to HTML.
        if full_path.startswith(("api/", "artifacts/", "pagoda3/", "pagoda3-store/", "viewer-launch")):
            raise HTTPException(404, "not found")
        candidate = _FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_FRONTEND_DIST / "index.html"),
                            headers=_INDEX_NO_CACHE)
