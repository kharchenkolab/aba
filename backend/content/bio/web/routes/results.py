"""Result endpoints (/api/results/*) + entity promotion / pin gestures
that wrap evidence into Results.

A Result is a kept observation: a grouping of one or more panels
(figure / table / value / text). Results sit "on the shelf" by virtue
of being Results — no explicit pinned flag needed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.web.deps import require_project
from core.graph.edges import add_edge, remove_edge
from core.graph.entities import create_entity, get_entity
from core.graph.messages import get_messages
from core.graph._schema import WORKSPACE_ID
from content.bio.graph.result_members import (
    add_result_member, remove_result_member, update_result_member,
    reorder_result_members,
)
from content.bio.lifecycle.promote import promote_figure_to_result
from content.bio.advisors.runner import skeptic_review

from ._helpers import _llm_figure_caption


router = APIRouter()


class MemberRequest(BaseModel):
    kind: str = "figure"            # figure | table | value | text
    ref: str | None = None          # cell entity id (for figure/table/value)
    text: str | None = None         # inline prose (for text panels)
    caption: str = ""
    caption_origin: str | None = None   # 'ai' | 'user' — set by UI on first edit
    at: int | None = None           # insert position (append if None)


class CreateResultRequest(BaseModel):
    thread_id: str = "default"
    title: str = "Result"
    interpretation: str = ""
    origin: str = "internal"
    members: list[MemberRequest] = []


class ReorderRequest(BaseModel):
    order: list[str] = []


def _result_or_404(rid: str) -> dict:
    e = get_entity(rid)
    if not e or e["type"] != "result":
        raise HTTPException(404, f"Result {rid} not found")
    return e


@router.post("/api/results")
def create_result(req: CreateResultRequest, _pid: str = Depends(require_project)):
    """Create a Result (an observation). Usually seeded with one cell; grows
    deliberately via add-member. Results are on the shelf by virtue of being
    Results — no explicit pinned flag needed."""
    from core.graph.derivation import derived_from, manual, human_actor
    _refs = [m.ref for m in req.members if getattr(m, "ref", None)]
    eid = create_entity(
        entity_type="result", title=req.title,
        derivation=derived_from(_refs) if _refs else manual(),   # Phase 2B
        actor=human_actor(),
        metadata={"thread_id": req.thread_id, "origin": req.origin,
                  "interpretation": req.interpretation,
                  "interpretation_origin": "user",
                  "members": []})
    for m in req.members:
        add_result_member(eid, kind=m.kind, ref=m.ref, text=m.text,
                          caption=m.caption, at=m.at)
        if m.ref:
            add_edge(eid, m.ref, "includes")
    return get_entity(eid)


@router.post("/api/results/{rid}/members")
def result_add_member(rid: str, req: MemberRequest, _pid: str = Depends(require_project)):
    _result_or_404(rid)
    out = add_result_member(rid, kind=req.kind, ref=req.ref, text=req.text,
                            caption=req.caption, at=req.at)
    if req.ref:
        add_edge(rid, req.ref, "includes")
    return out


@router.patch("/api/results/{rid}/members/{member_id}")
def result_update_member(rid: str, member_id: str, req: MemberRequest, _pid: str = Depends(require_project)):
    _result_or_404(rid)
    return update_result_member(rid, member_id, caption=req.caption,
                                text=req.text, caption_origin=req.caption_origin)


@router.delete("/api/results/{rid}/members/{member_id}")
def result_remove_member(rid: str, member_id: str, _pid: str = Depends(require_project)):
    e = _result_or_404(rid)
    ref = next((m.get("ref") for m in (e.get("metadata") or {}).get("members", [])
                if m.get("id") == member_id), None)
    out = remove_result_member(rid, member_id)
    if ref:
        remove_edge(rid, ref, "includes")
    return out


@router.post("/api/results/{rid}/reorder")
def result_reorder(rid: str, req: ReorderRequest, _pid: str = Depends(require_project)):
    _result_or_404(rid)
    return reorder_result_members(rid, req.order)


@router.post("/api/results/{rid}/regenerate-interpretation")
def regenerate_interpretation(rid: str, _pid: str = Depends(require_project)):
    """Re-fire the auto-interpret background job for a single Result. Used
    when the original schedule failed. Idempotent: auto_interpret skips
    if interpretation_origin=='user' (the user has edited)."""
    from content.bio.lifecycle.promote import auto_interpret
    r = get_entity(rid)
    if not r or r.get("type") != "result":
        raise HTTPException(404, f"result {rid} not found")
    text = auto_interpret(rid)
    return {"ok": True, "wrote": bool(text), "preview": (text or "")[:200]}


@router.post("/api/results/{rid}/synthesize")
def synthesize_result_route(rid: str, _pid: str = Depends(require_project)):
    """Generate (or re-generate) the Result's SYNTHESIS ACROSS PANELS via the Guide,
    for the green-star button. An explicit user action, so `force=True` — it overrides
    a prior AI synthesis (and a user-edited one, since the user asked to re-generate).
    Returns {ok, interpretation}. Synchronous — the UI shows a 'generating…' status
    while it awaits (a few seconds)."""
    from content.bio.lifecycle.promote import synthesize_result
    r = get_entity(rid)
    if not r or r.get("type") != "result":
        raise HTTPException(404, f"result {rid} not found")
    text = synthesize_result(rid, force=True)
    return {"ok": bool(text), "interpretation": text or ""}


# --- Promotion + pin gestures (figure → result; pin/unpin entities) ---


class PromoteFigureRequest(BaseModel):
    interpretation: str
    title: str | None = None


@router.post("/api/entities/{figure_id}/promote-to-result")
async def promote_to_result(figure_id: str, req: PromoteFigureRequest, _pid: str = Depends(require_project)):
    import asyncio
    try:
        rid = promote_figure_to_result(figure_id, req.interpretation, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Fire the Skeptic asynchronously so the user gets the result back
    # promptly. The note shows up when the advisor rail next reloads.
    asyncio.get_event_loop().run_in_executor(None, skeptic_review, rid)
    return get_entity(rid)


@router.post("/api/entities/{entity_id}/pin")
def pin_entity_to_result(entity_id: str, _pid: str = Depends(require_project)):
    """EntityMenu Pin: promote this existing evidence entity (figure /
    table / cell / note / narrative) into a Result. Result is created
    immediately with a placeholder interpretation; a background job
    replaces it with the Guide's adjacent narration."""
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
        interpretation=None,
        origin=(ent.get("metadata") or {}).get("origin", "internal"),
    )
    # SYNC endpoint, so asyncio.get_event_loop() doesn't work here.
    # Use a plain Thread for the background interpretation job —
    # fire-and-forget, daemon so it doesn't block shutdown.
    import threading
    threading.Thread(target=auto_interpret, args=(out["result_id"],),
                     daemon=True).start()
    return get_entity(out["result_id"])


@router.post("/api/entities/{entity_id}/unpin")
def unpin_entity(entity_id: str, _pid: str = Depends(require_project)):
    """Inverse of /pin — archive the wrapping Result(s) if this is the
    only evidence, else just remove this evidence as a member."""
    from content.bio.lifecycle.promote import unpin_evidence
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    tid = (ent.get("metadata") or {}).get("thread_id")
    return unpin_evidence(entity_id, thread_id=tid)


# --- Entity history, provenance, suggest-interpretation (figure-focused) ---


@router.get("/api/entities/{entity_id}/history")
def entities_history(entity_id: str):
    """Version chain for a figure (newest first)."""
    from content.bio.graph.figure_history import figure_history
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return figure_history(entity_id)


@router.get("/api/entities/{entity_id}/provenance")
def entities_provenance(entity_id: str):
    """Full provenance-EVIDENCE for the card's Provenance section: method (code/
    command/recipe), inputs (datasets + versions), environment (language + packages),
    attribution (who/when), lineage (up/down with edge labels), reproducibility.
    Assembled from derivation+actor + the exec record + the edge graph. Keeps the
    flat `upstream`/`downstream`/`promotion` keys for back-compat."""
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    from core.graph.provenance_evidence import evidence
    out = evidence(entity_id)
    if out is None:
        raise HTTPException(404, f"Entity {entity_id} not found")
    return out


@router.get("/api/entities/{entity_id}/suggest-interpretation")
def suggest_interpretation(entity_id: str):
    """Generate a structured figure caption for promoting a figure → result.

    Calls the live LLM with VISION + the figure's producing_code + nearby
    chat context. The caption has two sections:
      - **What's shown** — panel-by-panel description.
      - **Take-home** — 1-3 bullets stating what the figure demonstrates.

    Falls back to plucking nearby chat text if the LLM call fails or
    vision can't read the file."""
    import json
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
            if not t:
                continue
            role = msgs[j]["role"]
            tag = "USER" if role == "user" else "AGENT"
            anchor = " (← figure here)" if j == prod_idx else ""
            chunks.append(f"[{tag}{anchor}] {t}")
        chat_context = "\n\n".join(chunks)[:3000]

    # Post-cutover: code resolved via the exec record (Stage 2 of
    # misc/exec_records_and_versioning.md). Legacy entities fall back
    # to their producing_code column inside the helper.
    from core.graph.exec_records import lookup_code_for_entity
    producing_code = lookup_code_for_entity(e)[:6000]
    title = (e.get("title") or "").strip()

    text = _llm_figure_caption(art, producing_code, chat_context, title)

    # Fallback to text-pluck.
    if not text:
        if prod_idx is not None:
            for j in range(prod_idx, min(prod_idx + 4, len(msgs))):
                t = asst_text(msgs[j])
                if t:
                    text = t
                    break
        if not text:
            for m in reversed(msgs):
                t = asst_text(m)
                if t:
                    text = t
                    break
    return {"text": text[:1200]}
