"""Bio HTTP routes. arch3.md Phase 8 — moved out of backend/main.py.

Each endpoint registers on the package-level `router` (a FastAPI
APIRouter). main.py mounts via `app.include_router(router)`. Behavioral
parity with the pre-split main.py is the success criterion.

This is the FIRST cluster moved: claims (/api/claims/*). Subsequent
commits will add results, findings, datasets, runs, etc. The pattern
established here is what each follow-up uses verbatim.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
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
from content.bio.advisors.runner import skeptic_review, explorer_suggest, stylist_review
from core.graph.audit import (
    list_advisor_notes, set_advisor_note_status,
    list_context_suggestions, update_context_suggestion_status,
    reject_all_pending_suggestions,
)
from core.graph.messages import get_messages
from core.graph._schema import WORKSPACE_ID


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


# ── Revision navigation + operations (Stage 5 of
# misc/exec_records_and_versioning.md) ───────────────────────────────────────

@router.get("/api/entities/{entity_id}/revisions")
def list_revisions(entity_id: str):
    """Return the revision chain for a figure/table entity, newest first.

    Follows wasRevisionOf edges in both directions from `entity_id` so
    the same chain is returned regardless of which revision the caller
    happens to be looking at. The 'position' field is the 0-based index
    of `entity_id` within the chain (0 = newest).
    """
    from content.bio.graph.figure_history import figure_history
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    chain = figure_history(entity_id)
    pos = next((i for i, e in enumerate(chain) if e["id"] == entity_id), 0)
    return {
        "chain": chain,
        "position": pos,
        "prev": chain[pos + 1]["id"] if pos + 1 < len(chain) else None,
        "next": chain[pos - 1]["id"] if pos > 0 else None,
    }


class MakeRevisionRequest(BaseModel):
    modified_code: str
    title: str | None = None


@router.post("/api/entities/{entity_id}/make_revision")
def make_revision_endpoint(entity_id: str, req: MakeRevisionRequest):
    """Run `modified_code` and pin the new artifact as wasRevisionOf
    `entity_id`. Both stay pinned siblings — the original is NOT
    auto-superseded. Returns the new entity record (figure or table).
    """
    from content.bio.lifecycle.revisions import make_revision
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    try:
        out = make_revision(
            entity_id, req.modified_code,
            title=req.title,
            thread_id=(ent.get("metadata") or {}).get("thread_id"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    new_ent = get_entity(out["new_entity_id"])
    return {
        "entity": new_ent,
        "exec_id": out.get("exec_id"),
        "wasRevisionOf": out.get("wasRevisionOf"),
    }


@router.post("/api/entities/{entity_id}/reproduce")
def reproduce_endpoint(entity_id: str):
    """Re-run the exec that produced `entity_id` and report the result.

    Doesn't create any new entity — just runs and returns the reproduction
    summary (new_exec_id, env_drift flag, fingerprints, warnings). The
    caller may follow up with /make_revision to pin the reproduction as
    a sibling if they want.
    """
    from content.bio.lifecycle.revisions import reproduce_from_exec
    ent = get_entity(entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    try:
        return reproduce_from_exec(
            entity_id,
            thread_id=(ent.get("metadata") or {}).get("thread_id"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


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


# ============================================================================
# Phase 8.C — Runs + Datasets
# ============================================================================


# Path-collision helpers now live in core.data.paths.unique_path /
# unique_dir_path (Block 1B — were duplicated between main.py and here).
from core.data.paths import unique_path as _unique_path  # noqa: E402
from core.data.paths import unique_dir_path as _unique_dir_path  # noqa: E402


def _refresh_dataset_layout_hint(bundle: Path) -> str:
    try:
        from content.bio.tools import _dataset_layout_hint
        return _dataset_layout_hint(str(bundle))
    except Exception:
        return ""


def _dataset_bytes_and_count(bundle: Path) -> tuple[int, int]:
    total, count = 0, 0
    if not bundle.is_dir():
        return (total, count)
    for p in bundle.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
            count += 1
    return (total, count)


def _run_or_404(rid: str) -> dict:
    e = get_entity(rid)
    if not e or e["type"] != "analysis":
        raise HTTPException(404, f"Run {rid} not found")
    return e


# --- Runs ---


@router.post("/api/runs/{rid}/refresh-manifest")
def runs_refresh_manifest(rid: str):
    """Re-scan a Run's output dir and rebuild its manifest. Useful after
    a server-side change to the manifester (e.g. new PDF-thumbnail support)."""
    from content.bio.lifecycle.runs import refresh_output_manifest
    e = get_entity(rid)
    if not e or e.get("type") != "analysis":
        raise HTTPException(404, f"run {rid} not found")
    refresh_output_manifest(rid)
    return {"ok": True}


@router.post("/api/runs/{rid}/cancel")
def run_cancel(rid: str):
    e = _run_or_404(rid)
    meta = dict(e.get("metadata") or {})
    run = dict(meta.get("run") or {})
    run["status"] = "cancelled"
    run["finished_at"] = _now()
    meta["run"] = run
    return update_entity(rid, metadata=meta)


class PinOutputRequest(BaseModel):
    kind: str = "figure"
    label: str = ""
    thumb: str | None = None
    href: str | None = None
    size: str | None = None
    interpretation: str = ""


@router.post("/api/runs/{rid}/pin-output")
def run_pin_output(rid: str, req: PinOutputRequest):
    """Pin one of a run's outputs as a Result wrapping the evidence
    (figure/table). Plots/tables we can render are kept with their
    thumbnail; everything else is a reference (origin=external + href)
    — we don't host a copy."""
    from content.bio.lifecycle.promote import pin_evidence
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id") or ""
    etype = "table" if req.kind == "table" else "figure"
    is_img = bool(req.thumb) and req.thumb.lower().rsplit(".", 1)[-1] in (
        "png", "jpg", "jpeg", "svg", "webp", "gif")
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


@router.post("/api/runs/{rid}/register-dataset")
def run_register_dataset(rid: str, req: RegisterDatasetRequest):
    """Lift a run's PRIMARY artifact (e.g. a processed-data bundle) into
    a first-class Dataset entity — by reference: we record where it
    lives, we do not host a copy."""
    run = _run_or_404(rid)
    tid = (run.get("metadata") or {}).get("thread_id")
    # By-reference datasets still have an artifact_path — it's the
    # remote/local path the data lives at. Satisfies dataset.yaml's
    # required field; the `by_reference`+`ref_path` metadata makes
    # the semantics explicit (we don't host a local copy).
    ref_path = req.path or ""
    eid = create_entity(
        entity_type="dataset", title=req.label or "dataset",
        artifact_path=ref_path or None,
        metadata={"thread_id": tid, "origin": "external", "by_reference": True,
                  "ref_path": ref_path, "size_label": req.size,
                  "summary": req.summary, "source_run": rid})
    add_edge(eid, rid, "produced_by")
    return get_entity(eid)


@router.get("/api/runs/{rid}/tree")
def run_tree(rid: str):
    """The Run's subtree from the files tree (its readme, code, output/
    dir + curated figures/tables) — so the Run view can embed the shared
    FileBrowser and browse nested output folders."""
    _run_or_404(rid)
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
        # Run exists but isn't placed in the tree yet (e.g. no outputs) —
        # empty root.
        return {"kind": "root", "name": "", "path": "", "children": []}
    return {**node, "kind": "root"}


@router.get("/api/runs/{rid}/file")
def run_file(rid: str, rel: str, download: int = 0):
    """Serve a single file from a Run's output directory (its artifact_path).
    Powers the Run view's output rows + figure thumbnails. `rel` is the
    path relative to the run dir; traversal outside the dir is rejected.
    Images/text render inline; `download=1` forces an attachment."""
    import mimetypes
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


# --- Datasets ---


@router.get("/api/datasets/{did}/tree")
def dataset_tree(did: str):
    """The dataset's subtree from the files tree (its directory contents,
    or the single registered file) — so the Dataset view can browse a
    folder dataset with the shared FileBrowser.

    Adds `is_directory: bool` to the root response — the authoritative
    signal of whether the dataset is shaped as a directory on disk."""
    ent = get_entity(did)
    if not ent or ent["type"] != "dataset":
        raise HTTPException(404, f"Dataset {did} not found")
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

    ap = ent.get("artifact_path")
    is_directory = bool(ap) and Path(ap).is_dir()

    node = _find(tree)
    if node is None:
        return {"kind": "root", "name": ent.get("title") or "dataset",
                "path": "", "children": [], "is_directory": is_directory}
    if node.get("kind") == "folder":
        return {**node, "kind": "root", "is_directory": True}
    # Single-file dataset → present the one file under a root.
    return {"kind": "root", "name": ent.get("title") or "dataset",
            "path": "", "children": [node], "is_directory": is_directory}


@router.post("/api/datasets")
async def datasets_create(req: dict | None = None):
    """Create an empty directory-shaped dataset entity. Body:
    {name?, project_id?}. The dataset folder is created on disk so
    subsequent upload-folder?append_to= calls can drop files into it."""
    from core.config import current_project_id, project_data_dir
    from core.web.deps import _pin_or_412
    body = req or {}
    _pin_or_412(body.get("project_id"))
    raw = (body.get("name") or "").strip() or "New dataset"
    safe = Path(raw).name.strip() or "New dataset"
    bundle = _unique_dir_path(project_data_dir(current_project_id()) / safe)
    bundle.mkdir(parents=True, exist_ok=True)
    eid = create_entity(
        entity_type="dataset", title=bundle.name, artifact_path=str(bundle),
        metadata={"size_bytes": 0, "file_count": 0, "layout": "directory",
                  "layout_hint": "", "original_name": raw},
    )
    return get_entity(eid)


@router.post("/api/upload-folder")
async def upload_folder(
    folder_name: str = Form(...),
    files: list[UploadFile] = File(...),
    rel_paths: list[str] = Form(...),
    append_to: str | None = Form(None),
    project_id: str | None = Form(None),
):
    """Upload N files as ONE directory-shaped dataset entity, preserving
    the folder layout. If `append_to=<dataset_id>`, files are appended
    to that existing dataset; the dataset's size/file_count/layout_hint
    are refreshed. Returns the (created or updated) entity."""
    from core.config import current_project_id, project_data_dir
    from core.web.deps import _pin_or_412
    _pin_or_412(project_id)
    if not files:
        raise HTTPException(400, "no files in upload")
    if len(files) != len(rel_paths):
        raise HTTPException(400, "files and rel_paths length mismatch")

    appending = bool(append_to)
    if appending:
        existing = get_entity(append_to)
        if not existing or existing["type"] != "dataset":
            raise HTTPException(404, f"Dataset {append_to} not found")
        ap = existing.get("artifact_path") or ""
        if not ap or (Path(ap).exists() and not Path(ap).is_dir()):
            raise HTTPException(400, "cannot append to a single-file dataset")
        bundle = Path(ap)
        bundle.mkdir(parents=True, exist_ok=True)
        if (existing.get("metadata") or {}).get("layout") != "directory":
            meta = dict((existing.get("metadata") or {}))
            meta["layout"] = "directory"
            update_entity(append_to, metadata=meta)
    else:
        safe = Path(folder_name).name.strip() or "uploaded_folder"
        bundle = _unique_dir_path(project_data_dir(current_project_id()) / safe)
        bundle.mkdir(parents=True, exist_ok=True)

    written = 0
    for f, rel in zip(files, rel_paths):
        rel_clean = Path(rel).as_posix().lstrip("/")
        if not rel_clean or ".." in rel_clean.split("/"):
            continue
        dest = bundle / rel_clean
        if appending and dest.exists():
            dest = _unique_path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        written += 1

    if written == 0:
        if not appending:
            try: bundle.rmdir()
            except OSError: pass
        raise HTTPException(400, "no valid file paths in upload")

    total_bytes, file_count = _dataset_bytes_and_count(bundle)
    hint = _refresh_dataset_layout_hint(bundle)

    if appending:
        meta = dict((existing.get("metadata") or {}))
        meta.update({"size_bytes": total_bytes, "file_count": file_count,
                     "layout": "directory", "layout_hint": hint})
        update_entity(append_to, metadata=meta)
        return get_entity(append_to)

    eid = create_entity(
        entity_type="dataset", title=bundle.name, artifact_path=str(bundle),
        metadata={"size_bytes": total_bytes, "file_count": file_count,
                  "layout": "directory", "layout_hint": hint,
                  "original_name": folder_name},
    )
    return get_entity(eid)


# ============================================================================
# Phase 8.D — Proposals + Advisor-notes + Context-suggestions + Advise +
#             Suggest-interpretation
# ============================================================================


@router.post("/api/proposals/{pid}/accept")
def proposal_accept(pid: int):
    from content.bio.proposals.scheduler import accept_proposal
    try:
        return accept_proposal(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/api/proposals/{pid}/dismiss")
def proposal_dismiss(pid: int):
    from content.bio.proposals.scheduler import dismiss_proposal
    return dismiss_proposal(pid)


@router.post("/api/proposals/{pid}/undo")
def proposal_undo(pid: int):
    from content.bio.proposals.scheduler import undo_proposal
    try:
        return undo_proposal(pid)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/api/entities/{entity_id}/advisor-notes")
def entities_advisor_notes(entity_id: str):
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return list_advisor_notes(entity_id)


class AdvisorNoteStatusRequest(BaseModel):
    status: str = "dismissed"


@router.post("/api/advisor-notes/{note_id}/status")
def advisor_note_status(note_id: int, req: AdvisorNoteStatusRequest):
    """Mark a note tried/dismissed so it no longer surfaces as a fresh idea."""
    if not set_advisor_note_status(note_id, req.status):
        raise HTTPException(404, f"Note {note_id} not found")
    return {"ok": True}


@router.post("/api/entities/{entity_id}/advise")
async def entities_advise(entity_id: str):
    """Fire the appropriate on-focus advisor for an entity (Explorer for
    datasets, Stylist for narratives). Idempotent — advisors that have
    already spoken about the entity won't re-fire. Non-blocking."""
    import asyncio
    e = get_entity(entity_id)
    if not e:
        raise HTTPException(404, f"Entity {entity_id} not found")
    loop = asyncio.get_event_loop()
    if e["type"] == "dataset":
        loop.run_in_executor(None, explorer_suggest, entity_id)
    elif e["type"] == "narrative":
        loop.run_in_executor(None, stylist_review, entity_id)
    return {"ok": True}


# --- Adaptive context (suggestions for per-type policy text) ---


@router.get("/api/context-suggestions")
def context_suggestions(status: str = "pending", max_age_days: int = 14):
    """List context-policy suggestions awaiting review (or any status).

    `max_age_days` defaults to 14 — older pending items auto-stale out of
    the badge / list. Pass 0 (or any non-positive) to include every age."""
    return list_context_suggestions(
        status=status,
        max_age_days=max_age_days if max_age_days > 0 else None,
    )


class SuggestionAction(BaseModel):
    action: str  # 'approve' | 'reject'


@router.post("/api/context-suggestions/{sid}/action")
def context_suggestion_action(sid: int, req: SuggestionAction):
    """Apply a reviewer action:
      approve → status='promoted' + append to the per-type policy file
      reject  → status='rejected'"""
    from content.bio.lifecycle.adaptive import append_to_policy
    if req.action not in ("approve", "reject"):
        raise HTTPException(400, "action must be 'approve' or 'reject'")
    pending = [s for s in list_context_suggestions(status=None, max_age_days=None)
               if s["id"] == sid]
    if not pending:
        raise HTTPException(404, f"suggestion {sid} not found")
    suggestion = pending[0]
    if req.action == "approve":
        append_to_policy(suggestion["entity_type"], suggestion["suggestion"])
        update_context_suggestion_status(sid, "promoted")
    else:
        update_context_suggestion_status(sid, "rejected")
    return {"ok": True}


@router.post("/api/context-suggestions/reject-all")
def context_suggestion_reject_all():
    """Bulk-reject every pending suggestion (any age). Returns count rejected."""
    return {"rejected": reject_all_pending_suggestions()}


# --- Vision-LLM figure caption (figure → result interpretation seed) ---


def _artifact_url_to_path(url: str):
    """Resolve a /artifacts/<pid>/<name> URL to a disk Path, or None.
    Local copy — main.py has its own for the /artifacts/* GET route;
    these will dedupe when an artifact-resolver moves to core."""
    import json  # noqa: F401 (kept consistent with main.py's import block)
    if not url:
        return None
    if url.startswith("/artifacts/"):
        parts = url[len("/artifacts/"):].split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            from core.config import project_artifacts_dir
            return project_artifacts_dir(parts[0]) / parts[1]
        if len(parts) == 1:
            from core.config import ARTIFACTS_DIR
            return ARTIFACTS_DIR / parts[0]
        return None
    return Path(url) if url else None


def _llm_figure_caption(artifact_path: str, producing_code: str,
                        chat_context: str, title: str) -> str:
    """Thin wrapper around the shared vision-LLM caption helper."""
    from content.bio.lifecycle.promote import caption_via_vision_llm
    disk = _artifact_url_to_path(artifact_path) if artifact_path else None
    return caption_via_vision_llm(disk, producing_code, chat_context, title)


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


# ============================================================================
# Phase 8.E — Thread-bio + Home-summary + Scenarios + Provenance +
#             remaining bio file/result endpoints
# ============================================================================


class EvaluateRequest(BaseModel):
    trigger: str = "post_turn"


@router.get("/api/threads/{tid}/proposals")
def thread_proposals(tid: str, status: str = "pending"):
    from core.graph.proposals_store import list_proposals
    rtid = _resolve_thread(tid)
    return list_proposals(thread_id=rtid, status=(status or None))


@router.post("/api/threads/{tid}/evaluate")
def thread_evaluate(tid: str, req: EvaluateRequest):
    """Run proposal detectors for a thread on demand (used by the
    thread-open event trigger). Post-turn evaluation fires from guide.py."""
    from content.bio.proposals.scheduler import evaluate_thread
    from core.graph.proposals_store import list_proposals
    rtid = _resolve_thread(tid)
    evaluate_thread(rtid, req.trigger)
    return list_proposals(thread_id=rtid, status="pending")


@router.post("/api/threads/{tid}/orient")
def thread_orient(tid: str):
    """Cold-start orientation: the Guide summarizes the project's data +
    suggests next steps. Idempotent — no-ops once the thread has a
    conversation or has already been oriented."""
    from content.bio.lifecycle.orientation import orient_thread
    rtid = _resolve_thread(tid)
    result = orient_thread(rtid)
    return {"oriented": bool(result), "result": result}


@router.get("/api/entities/{entity_id}/history")
def entities_history(entity_id: str):
    """Version chain for a figure (newest first)."""
    from content.bio.graph.figure_history import figure_history
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    return figure_history(entity_id)


@router.get("/api/entities/{entity_id}/provenance")
def entities_provenance(entity_id: str):
    """Upstream/downstream neighborhood for the canvas Provenance panel."""
    if not get_entity(entity_id):
        raise HTTPException(404, f"Entity {entity_id} not found")
    from core.graph.provenance import neighborhood
    return neighborhood(entity_id)


# --- Scenarios ---


class ScenarioRequest(BaseModel):
    description: str
    code: str | None = None
    title: str | None = None


@router.post("/api/entities/{baseline_id}/create-scenario")
async def create_scenario(baseline_id: str, req: ScenarioRequest):
    import asyncio
    from content.bio.lifecycle.scenarios import create_scenario_variant
    try:
        new_entity = await asyncio.get_event_loop().run_in_executor(
            None,
            create_scenario_variant,
            baseline_id, req.description, req.code, req.title,
        )
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e))
    return new_entity


# --- Result uploads (file-form endpoints) ---


@router.post("/api/results/external")
async def upload_external_result(
    file: UploadFile = File(...),
    thread_id: str = Form("default"),
    interpretation: str = Form(""),
):
    """Bring in an external result (a gel, a wet-lab readout, a figure
    from another tool) as a first-class Result wrapping the upload."""
    from content.bio.lifecycle.promote import pin_evidence
    from content.bio.proposals.scheduler import evaluate_thread
    from core.config import current_project_id, project_artifacts_dir
    if not file.filename:
        raise HTTPException(400, "filename missing")
    pid = current_project_id()
    dest = _unique_path(project_artifacts_dir(pid) / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = _resolve_thread(thread_id)
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
    evaluate_thread(tid, "data_upload")
    return get_entity(out["result_id"])


@router.post("/api/results/{rid}/upload-evidence")
async def result_upload_evidence(
    rid: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
):
    """Result-page Add-evidence: upload a file and append it as a NEW
    member of this existing Result. Interpretation is NOT regenerated."""
    from content.bio.lifecycle.promote import pin_evidence
    from core.config import current_project_id, project_artifacts_dir
    r = _result_or_404(rid)
    if not file.filename:
        raise HTTPException(400, "filename missing")
    pid = current_project_id()
    dest = _unique_path(project_artifacts_dir(pid) / Path(file.filename).name)
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    tid = (r.get("metadata") or {}).get("thread_id") or ""
    pin_evidence(
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


# --- Project overview + onboarding ---


@router.get("/api/home-summary")
def home_summary(project_id: str | None = None):
    """Dashboard data for Home: counts, recent activity, attention.
    `project_id` pins per-request so Home can preview any project."""
    from core.web.deps import _pin_or_412
    from core.graph.entities import list_entities, count_entities  # noqa
    from core.graph.audit import list_events
    from core.graph.jobs import list_jobs
    _pin_or_412(project_id)
    ents = list_entities(exclude_workspace=True, include_archived=False)
    counts: dict[str, int] = {}
    for e in ents:
        if e["status"] in ("superseded",):
            continue
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    jobs = list_jobs(limit=100)
    suggestions = list_context_suggestions(status="pending")
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


@router.post("/api/sample-project")
def sample_project():
    """One-click sample: register the bundled cells.csv as a dataset."""
    from core.config import current_project_id, project_data_dir
    src = Path(__file__).resolve().parents[3] / "data" / "cells.csv"
    if not src.exists():
        raise HTTPException(500, "sample data missing")
    dest = _unique_path(project_data_dir(current_project_id()) / "sample_cells.csv")
    shutil.copyfile(src, dest)
    eid = create_entity(
        entity_type="dataset", title=dest.name, artifact_path=str(dest),
        metadata={"size_bytes": dest.stat().st_size, "sample": True},
    )
    return get_entity(eid)
