"""Bio HTTP routes. arch3.md Phase 8 — moved out of backend/main.py.

Each endpoint registers on the package-level `router` (a FastAPI
APIRouter). main.py mounts via `app.include_router(router)`. Behavioral
parity with the pre-split main.py is the success criterion.

This is the FIRST cluster moved: claims (/api/claims/*). Subsequent
commits will add results, findings, datasets, runs, etc. The pattern
established here is what each follow-up uses verbatim.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.graph._schema import gen_entity_id
from core.graph.edges import add_edge, remove_edge
from core.graph.entities import create_entity, get_entity, update_entity
from content.bio.graph.result_members import (
    add_result_member, remove_result_member, update_result_member,
    reorder_result_members,
)
from content.bio.lifecycle.promote import (
    promote_figure_to_result, promote_results_to_finding,
    add_result_to_finding, remove_result_from_finding,
)
from content.bio.advisors.runner import skeptic_review


router = APIRouter()


# Claim confidence ladder — the bio-specific values (entity_model_v3 §2,
# claim.yaml's confidence_model). Validated at the status-transition
# endpoint below.
CONFIDENCE = ("preliminary", "supported", "validated", "contested", "refuted")


# --- Small local helpers (kept here to avoid main↔bio import cycles).
# `_now` is a 2-line datetime stamp; `_resolve_thread` resolves the
# special "default" token. Both have copies in main.py serving the
# remaining in-main handlers — they're tiny and unlikely to drift.


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_thread(thread_id: str) -> str:
    if thread_id == "default":
        from core.graph.threads import get_or_create_default_thread
        return get_or_create_default_thread()
    return thread_id


def _claim_or_404(cid: str) -> dict:
    ent = get_entity(cid)
    if not ent or ent["type"] != "claim":
        raise HTTPException(404, f"Claim {cid} not found")
    return ent


def _save_claim(cid: str, ent: dict, updates: dict) -> dict:
    meta = dict(ent.get("metadata") or {})
    meta.update(updates)
    return update_entity(cid, metadata=meta)


# --- Pydantic request models ---


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


# --- /api/claims/* handlers ---


@router.post("/api/claims")
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


@router.patch("/api/claims/{cid}")
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


@router.post("/api/claims/{cid}/evidence")
def claim_add_evidence(cid: str, req: EvidenceRequest):
    ent = _claim_or_404(cid)
    ev = list((ent.get("metadata") or {}).get("evidence_ids") or [])
    if req.result_id not in ev:
        ev.append(req.result_id)
        add_edge(cid, req.result_id, "supports")
    return _save_claim(cid, ent, {"evidence_ids": ev})


@router.delete("/api/claims/{cid}/evidence/{rid}")
def claim_del_evidence(cid: str, rid: str):
    ent = _claim_or_404(cid)
    ev = [x for x in ((ent.get("metadata") or {}).get("evidence_ids") or []) if x != rid]
    remove_edge(cid, rid, "supports")
    return _save_claim(cid, ent, {"evidence_ids": ev})


@router.post("/api/claims/{cid}/caveats")
def claim_add_caveat(cid: str, req: CaveatRequest):
    ent = _claim_or_404(cid)
    cavs = list((ent.get("metadata") or {}).get("caveats") or [])
    cav = {"id": gen_entity_id("cav"), "text": req.text.strip(),
           "source": req.source, "dismissed": False, "at": _now()}
    cavs.append(cav)
    _save_claim(cid, ent, {"caveats": cavs})
    return cav


@router.patch("/api/claims/{cid}/caveats/{caid}")
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


@router.delete("/api/claims/{cid}/caveats/{caid}")
def claim_del_caveat(cid: str, caid: str):
    ent = _claim_or_404(cid)
    cavs = [c for c in ((ent.get("metadata") or {}).get("caveats") or []) if c.get("id") != caid]
    _save_claim(cid, ent, {"caveats": cavs})
    return {"ok": True}


@router.post("/api/claims/{cid}/alternatives")
def claim_add_alt(cid: str, req: AltRequest):
    ent = _claim_or_404(cid)
    alts = list((ent.get("metadata") or {}).get("alternatives") or [])
    alt = {"id": gen_entity_id("alt"), "text": req.text.strip(),
           "source": req.source, "status": "open", "at": _now()}
    alts.append(alt)
    _save_claim(cid, ent, {"alternatives": alts})
    return alt


@router.patch("/api/claims/{cid}/alternatives/{aid}")
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


@router.post("/api/claims/{cid}/alternatives/{aid}/promote")
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


@router.delete("/api/claims/{cid}/alternatives/{aid}")
def claim_del_alt(cid: str, aid: str):
    ent = _claim_or_404(cid)
    alts = [a for a in ((ent.get("metadata") or {}).get("alternatives") or []) if a.get("id") != aid]
    _save_claim(cid, ent, {"alternatives": alts})
    return {"ok": True}


@router.post("/api/claims/{cid}/status")
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


# ============================================================================
# Phase 8.B-1 — Results CRUD (kept observations, a grouping of panels)
# ============================================================================


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
        add_result_member(eid, kind=m.kind, ref=m.ref, text=m.text,
                          caption=m.caption, at=m.at)
        if m.ref:
            add_edge(eid, m.ref, "includes")
    return get_entity(eid)


@router.post("/api/results/{rid}/members")
def result_add_member(rid: str, req: MemberRequest):
    _result_or_404(rid)
    out = add_result_member(rid, kind=req.kind, ref=req.ref, text=req.text,
                            caption=req.caption, at=req.at)
    if req.ref:
        add_edge(rid, req.ref, "includes")
    return out


@router.patch("/api/results/{rid}/members/{member_id}")
def result_update_member(rid: str, member_id: str, req: MemberRequest):
    _result_or_404(rid)
    return update_result_member(rid, member_id, caption=req.caption,
                                text=req.text, caption_origin=req.caption_origin)


@router.delete("/api/results/{rid}/members/{member_id}")
def result_remove_member(rid: str, member_id: str):
    e = _result_or_404(rid)
    ref = next((m.get("ref") for m in (e.get("metadata") or {}).get("members", [])
                if m.get("id") == member_id), None)
    out = remove_result_member(rid, member_id)
    if ref:
        remove_edge(rid, ref, "includes")
    return out


@router.post("/api/results/{rid}/reorder")
def result_reorder(rid: str, req: ReorderRequest):
    _result_or_404(rid)
    return reorder_result_members(rid, req.order)


@router.post("/api/results/{rid}/regenerate-interpretation")
def regenerate_interpretation(rid: str):
    """Re-fire the auto-interpret background job for a single Result. Used
    when the original schedule failed. Idempotent: auto_interpret skips
    if interpretation_origin=='user' (the user has edited)."""
    from content.bio.lifecycle.promote import auto_interpret
    r = get_entity(rid)
    if not r or r.get("type") != "result":
        raise HTTPException(404, f"result {rid} not found")
    text = auto_interpret(rid)
    return {"ok": True, "wrote": bool(text), "preview": (text or "")[:200]}


# ============================================================================
# Phase 8.B-2 — Promotion chain (figure → result → finding) + pin gestures
# ============================================================================


class PromoteFigureRequest(BaseModel):
    interpretation: str
    title: str | None = None


class PromoteResultsRequest(BaseModel):
    result_ids: list[str]
    text: str
    title: str | None = None


@router.post("/api/entities/{figure_id}/promote-to-result")
async def promote_to_result(figure_id: str, req: PromoteFigureRequest):
    import asyncio
    try:
        rid = promote_figure_to_result(figure_id, req.interpretation, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Fire the Skeptic asynchronously so the user gets the result back
    # promptly. The note shows up when the advisor rail next reloads.
    asyncio.get_event_loop().run_in_executor(None, skeptic_review, rid)
    return get_entity(rid)


@router.post("/api/findings")
def create_finding(req: PromoteResultsRequest):
    try:
        fid = promote_results_to_finding(req.result_ids, req.text, req.title)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return get_entity(fid)


class NarrativeRequest(BaseModel):
    title: str
    text: str = ""


@router.post("/api/narratives")
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
    text: str = ""                 # concatenated text of selected messages
    title_hint: str = ""           # e.g. the first user message in the selection
    image_urls: list[str] = []     # figure/plot urls in the selection


@router.post("/api/findings/draft")
def draft_finding(req: DraftFindingRequest):
    """Selection-to-finding draft. Heuristic for now (no tokens): title
    from the ask, summary from the discussion, evidence resolved from
    the figures referenced in the selection."""
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


@router.post("/api/findings/from-draft")
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


@router.post("/api/findings/{finding_id}/fields")
def finding_fields(finding_id: str, req: FindingFieldsRequest):
    from content.bio.lifecycle.promote import set_finding_fields
    try:
        return set_finding_fields(finding_id, summary=req.summary,
                                  caveats=req.caveats, status=req.status,
                                  title=req.title)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/findings/{finding_id}/add-result")
def finding_add_result(finding_id: str, req: FindingResultRequest):
    try:
        return add_result_to_finding(finding_id, req.result_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/findings/{finding_id}/remove-result")
def finding_remove_result(finding_id: str, req: FindingResultRequest):
    try:
        return remove_result_from_finding(finding_id, req.result_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# --- Pin gestures: entities/pin, entities/unpin, messages/pin ---


@router.post("/api/entities/{entity_id}/pin")
def pin_entity_to_result(entity_id: str):
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
def unpin_entity(entity_id: str):
    """Inverse of /pin — archive the wrapping Result(s) if this is the
    only evidence, else just remove this evidence as a member."""
    from content.bio.lifecycle.promote import unpin_evidence
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    tid = (ent.get("metadata") or {}).get("thread_id")
    return unpin_evidence(entity_id, thread_id=tid)


class PinMessageRequest(BaseModel):
    key: str                       # stable content hash from the client
    text: str = ""
    title: str = ""
    image_urls: list[str] = []
    thread_id: str = "default"


@router.post("/api/messages/pin")
def pin_message(req: PinMessageRequest):
    """Pin a chat message: create a Note from the text + image_urls and
    wrap it in a Result. Toggles by content key — re-pinning the same
    message archives its Note."""
    from content.bio.graph.search import find_kept_note
    from content.bio.lifecycle.promote import pin_evidence
    existing = find_kept_note(req.key)
    if existing:
        update_entity(existing, status="archived")
        return {"pinned": False}
    tid = _resolve_thread(req.thread_id)
    title = (req.title or req.text).strip().split("\n")[0][:70] or "Kept note"
    out = pin_evidence(
        thread_id=tid, target_result_id=None,
        evidence_kind="note",
        evidence_payload={
            "title": title,
            "metadata": {"source_key": req.key, "text": req.text,
                         "image_urls": req.image_urls},
        },
        interpretation=req.text[:500] or None,
        origin="internal",
    )
    return {"pinned": True, "id": out["evidence_id"], "result_id": out["result_id"]}
