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
