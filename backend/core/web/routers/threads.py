"""Thread routes — list/create/patch + per-thread open-questions CRUD (incl.
promote-to-thread). Extracted from main.py (Item 2A.3). Domain-neutral
(core.graph.*). Mutating routes pin via Depends(require_project)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.graph.entities import get_entity, update_entity
from core.web.deps import require_project

router = APIRouter()


class ThreadRequest(BaseModel):
    title: str = ""
    question: str = ""
    question_source: str | None = None   # 'user' when the user typed the question
    # Primary spec to pin this thread to (e.g. "lean_guide"). None →
    # the thread falls through to ABA_PRIMARY_SPEC env / "guide"
    # default at chat time. See core.runtime.agent.resolve_spec_for_turn.
    spec: str | None = None


class ThreadPatch(BaseModel):
    title: str | None = None
    question: str | None = None
    open_questions: list[dict] | None = None
    lifecycle: str | None = None
    # Pin or clear the per-thread primary spec. Empty string clears
    # (UI dropdown reverts to "use default"). None leaves unchanged.
    spec: str | None = None


@router.get("/api/threads")
def threads_list():
    from core.graph.threads import list_threads
    return list_threads()


@router.post("/api/threads")
def threads_create(req: ThreadRequest, _pid: str = Depends(require_project)):
    from core.graph.threads import create_thread
    tid = create_thread(req.title, req.question, spec=req.spec)
    # A user-typed question is user-owned — keep the Guide from silently
    # rewriting it later.
    if req.question and req.question_source:
        ent = get_entity(tid)
        meta = dict(ent.get("metadata") or {})
        meta["question_source"] = req.question_source
        update_entity(tid, metadata=meta)
    return get_entity(tid)


@router.patch("/api/threads/{tid}")
def threads_patch(tid: str, req: ThreadPatch, _pid: str = Depends(require_project)):
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
    if req.spec is not None:
        # Empty string clears the pin (revert to env/default).
        if req.spec.strip():
            meta["spec"] = req.spec.strip()
        else:
            meta.pop("spec", None)
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


@router.post("/api/threads/{tid}/open-questions")
def oq_add(tid: str, req: OpenQRequest, _pid: str = Depends(require_project)):
    from core.graph._schema import gen_entity_id
    ent = _thread_or_404(tid)
    oqs = list((ent.get("metadata") or {}).get("open_questions") or [])
    oq = {"id": gen_entity_id("oq"), "text": req.text.strip(),
          "status": "open", "source": req.source,
          "at": datetime.now(timezone.utc).isoformat()}
    oqs.append(oq)
    _save_oqs(tid, ent, oqs)
    return oq


@router.patch("/api/threads/{tid}/open-questions/{oqid}")
def oq_patch(tid: str, oqid: str, req: OpenQPatch, _pid: str = Depends(require_project)):
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


@router.delete("/api/threads/{tid}/open-questions/{oqid}")
def oq_delete(tid: str, oqid: str, _pid: str = Depends(require_project)):
    ent = _thread_or_404(tid)
    oqs = [o for o in ((ent.get("metadata") or {}).get("open_questions") or [])
           if o.get("id") != oqid]
    _save_oqs(tid, ent, oqs)
    return {"ok": True}


@router.post("/api/threads/{tid}/open-questions/{oqid}/promote")
def oq_promote(tid: str, oqid: str, _pid: str = Depends(require_project)):
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
