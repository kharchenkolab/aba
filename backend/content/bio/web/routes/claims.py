"""Claim endpoints (/api/claims/*).

A Claim is a falsifiable statement with evidence, caveats, alternatives,
and a confidence ladder. See entity_model_v3 §2 / claim.yaml.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.web.deps import require_project
from core.graph._schema import gen_entity_id
from core.graph.edges import add_edge, remove_edge
from core.graph.entities import create_entity, get_entity, update_entity

from ._helpers import _now, _resolve_thread


router = APIRouter()


# Claim confidence ladder — the bio-specific values (entity_model_v3 §2,
# claim.yaml's confidence_model). Validated at the status-transition
# endpoint below.
CONFIDENCE = ("preliminary", "supported", "validated", "contested", "refuted")


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
def claim_create(req: ClaimRequest, _pid: str = Depends(require_project)):
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
def claim_patch(cid: str, req: ClaimPatch, _pid: str = Depends(require_project)):
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
def claim_add_evidence(cid: str, req: EvidenceRequest, _pid: str = Depends(require_project)):
    ent = _claim_or_404(cid)
    ev = list((ent.get("metadata") or {}).get("evidence_ids") or [])
    if req.result_id not in ev:
        ev.append(req.result_id)
        add_edge(cid, req.result_id, "supports")
    return _save_claim(cid, ent, {"evidence_ids": ev})


@router.delete("/api/claims/{cid}/evidence/{rid}")
def claim_del_evidence(cid: str, rid: str, _pid: str = Depends(require_project)):
    ent = _claim_or_404(cid)
    ev = [x for x in ((ent.get("metadata") or {}).get("evidence_ids") or []) if x != rid]
    remove_edge(cid, rid, "supports")
    return _save_claim(cid, ent, {"evidence_ids": ev})


@router.post("/api/claims/{cid}/caveats")
def claim_add_caveat(cid: str, req: CaveatRequest, _pid: str = Depends(require_project)):
    ent = _claim_or_404(cid)
    cavs = list((ent.get("metadata") or {}).get("caveats") or [])
    cav = {"id": gen_entity_id("cav"), "text": req.text.strip(),
           "source": req.source, "dismissed": False, "at": _now()}
    cavs.append(cav)
    _save_claim(cid, ent, {"caveats": cavs})
    return cav


@router.patch("/api/claims/{cid}/caveats/{caid}")
def claim_patch_caveat(cid: str, caid: str, req: CaveatPatch, _pid: str = Depends(require_project)):
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
def claim_del_caveat(cid: str, caid: str, _pid: str = Depends(require_project)):
    ent = _claim_or_404(cid)
    cavs = [c for c in ((ent.get("metadata") or {}).get("caveats") or []) if c.get("id") != caid]
    _save_claim(cid, ent, {"caveats": cavs})
    return {"ok": True}


@router.post("/api/claims/{cid}/alternatives")
def claim_add_alt(cid: str, req: AltRequest, _pid: str = Depends(require_project)):
    ent = _claim_or_404(cid)
    alts = list((ent.get("metadata") or {}).get("alternatives") or [])
    alt = {"id": gen_entity_id("alt"), "text": req.text.strip(),
           "source": req.source, "status": "open", "at": _now()}
    alts.append(alt)
    _save_claim(cid, ent, {"alternatives": alts})
    return alt


@router.patch("/api/claims/{cid}/alternatives/{aid}")
def claim_patch_alt(cid: str, aid: str, req: AltPatch, _pid: str = Depends(require_project)):
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
def claim_promote_alt(cid: str, aid: str, _pid: str = Depends(require_project)):
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
def claim_del_alt(cid: str, aid: str, _pid: str = Depends(require_project)):
    ent = _claim_or_404(cid)
    alts = [a for a in ((ent.get("metadata") or {}).get("alternatives") or []) if a.get("id") != aid]
    _save_claim(cid, ent, {"alternatives": alts})
    return {"ok": True}


@router.post("/api/claims/{cid}/status")
def claim_status(cid: str, req: StatusRequest, _pid: str = Depends(require_project)):
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
