"""
Promotion flows: figure → result → finding → claim.

Each promotion creates a new higher-level entity and edges it to its
source(s) with `supports` (for evidence) and `wasDerivedFrom` (so the
provenance walk works the same as for run-generated artifacts).
"""
from __future__ import annotations
import json
from typing import Optional, Any

from core.graph.edges import add_edge, remove_edge
from core.graph.entities import create_entity, get_entity, update_entity

# Placeholder used when interpretation is not provided at Pin time. The A3
# background Guide turn replaces this once the description has been generated.
AI_INTERPRETATION_PLACEHOLDER = "✨ generating…"


def pin_evidence(
    *,
    thread_id: str,
    target_result_id: Optional[str] = None,
    evidence_kind: str,                          # figure | table | cell | note | narrative | value | text
    evidence_id: Optional[str] = None,           # if the evidence already exists as an entity
    evidence_payload: Optional[dict[str, Any]] = None,  # else, create one from {title, artifact_path, metadata, …}
    caption: str = "",
    interpretation: Optional[str] = None,         # None → AI placeholder + A3 background job (TODO)
    origin: str = "internal",                    # "internal" | "external"
    parent_run_id: Optional[str] = None,         # optional 'produced_by' edge (run output pins)
    title: Optional[str] = None,                  # explicit Result title; else derived
) -> dict:
    """The ONE pin operation. Five UX surfaces (EntityMenu Pin, RunView output Pin,
    chat-message Pin, external upload, Result-page Add-evidence) all converge here.

    - When target_result_id is None, creates a NEW Result wrapping the evidence.
    - When target_result_id is set, appends the evidence as a NEW member to that
      existing Result; interpretation is NOT regenerated (member-add only).
    - Resolves the evidence: uses evidence_id if provided; else creates a new entity
      from evidence_payload (caller controls payload — file upload, message snippet, …).

    Returns {result_id, member_id, evidence_id} so callers know what was just attached.
    """
    from content.bio.graph.result_members import add_result_member

    # 1. Resolve / create the evidence entity.
    if evidence_id is None:
        if evidence_kind in ("text", "value"):
            # Text-only members don't get their own entity — they live as inline panels.
            # Skip creation; the member will carry `text` directly.
            evidence_id = None
        else:
            payload = dict(evidence_payload or {})
            md = dict(payload.get("metadata") or {})
            md.setdefault("thread_id", thread_id)
            md.setdefault("origin", origin)
            evidence_id = create_entity(
                entity_type=evidence_kind,
                title=payload.get("title") or evidence_kind,
                artifact_path=payload.get("artifact_path"),
                metadata=md,
            )
            if parent_run_id:
                add_edge(evidence_id, parent_run_id, "produced_by")
    elif not get_entity(evidence_id):
        raise ValueError(f"evidence {evidence_id} not found")

    member_kind = "figure" if evidence_kind in ("figure", "table", "cell", "value") else evidence_kind
    if member_kind not in ("figure", "table", "value", "text"):
        # Notes / narratives ride as "text" panels — the entity holds the prose,
        # the member carries the ref + caption.
        member_kind = "text"

    if target_result_id is not None:
        # Append to existing Result — interpretation untouched.
        r = get_entity(target_result_id)
        if not r or r["type"] != "result":
            raise ValueError(f"target result {target_result_id} not found")
        out = add_result_member(target_result_id, kind=member_kind, ref=evidence_id,
                                text=(evidence_payload or {}).get("text"),
                                caption=caption)
        if evidence_id:
            add_edge(target_result_id, evidence_id, "includes")
            add_edge(target_result_id, evidence_id, "supports", {"direction": "result-supported-by-evidence"})
        members = (out.get("metadata") or {}).get("members", []) if out else []
        member_id = members[-1].get("id") if members else None
        return {"result_id": target_result_id, "member_id": member_id, "evidence_id": evidence_id, "created_result": False}

    # New Result. Interpretation placeholder if none given → A3 background job
    # generates the real text and writes it back with interpretation_origin='ai'.
    interp = interpretation if interpretation is not None else AI_INTERPRETATION_PLACEHOLDER
    auto_title = title or (interp.strip().split("\n")[0][:80] if interp.strip() != AI_INTERPRETATION_PLACEHOLDER else "")
    if not auto_title:
        ev = get_entity(evidence_id) if evidence_id else None
        auto_title = (ev or {}).get("title") or "Result"
    rid = create_entity(
        entity_type="result",
        title=auto_title,
        parent_entity_id=(get_entity(evidence_id) or {}).get("parent_entity_id") if evidence_id else None,
        metadata={
            "thread_id": thread_id,
            "origin": origin,
            "interpretation": interp,
            "interpretation_origin": "ai" if interpretation is None else "user",
            "members": [],
        },
    )
    add_result_member(rid, kind=member_kind, ref=evidence_id,
                      text=(evidence_payload or {}).get("text"), caption=caption)
    if evidence_id:
        add_edge(rid, evidence_id, "includes")
        add_edge(rid, evidence_id, "supports", {"direction": "result-supported-by-evidence"})
        add_edge(rid, evidence_id, "wasDerivedFrom")
    if parent_run_id and evidence_id:
        add_edge(rid, parent_run_id, "wasDerivedFrom")
    final = get_entity(rid)
    members = (final.get("metadata") or {}).get("members", []) if final else []
    member_id = members[-1].get("id") if members else None
    return {"result_id": rid, "member_id": member_id, "evidence_id": evidence_id, "created_result": True}


def unpin_evidence(evidence_id: str, thread_id: Optional[str] = None) -> dict:
    """Inverse of pin_evidence (B / #321).

    For each non-archived Result in `thread_id` (or any thread, if None) that
    INCLUDES `evidence_id`:
      • exactly 1 member AND interpretation_origin == 'ai' (still auto)
          → ARCHIVE the Result (user never invested; hard-delete handled by
            the destructive-confirm in #322).
      • exactly 1 member AND interpretation_origin == 'user' (edited)
          → ARCHIVE the Result (preserve the user's interpretation; reversible).
      • >1 members → REMOVE only this member from the Result; Result stays.

    Returns {affected: [{result_id, action}]} for the UI/log.
    """
    from content.bio.graph.result_members import remove_result_member
    incoming = edges_to(evidence_id)
    wrap_ids = {e["source_id"] for e in incoming if e.get("rel_type") == "includes"}
    affected: list[dict] = []
    for rid in wrap_ids:
        r = get_entity(rid)
        if not r or r["type"] != "result" or r.get("status") == "archived":
            continue
        if thread_id and (r.get("metadata") or {}).get("thread_id") != thread_id:
            continue
        members = (r.get("metadata") or {}).get("members") or []
        # Identify which member(s) reference this evidence; usually exactly one.
        target_members = [m for m in members if m.get("ref") == evidence_id]
        if len(members) <= 1 or len(members) - len(target_members) == 0:
            origin = (r.get("metadata") or {}).get("interpretation_origin", "user")
            from core.graph.entities import archive_entity as _archive
            _archive(rid)
            remove_edge(rid, evidence_id, "includes")
            remove_edge(rid, evidence_id, "supports")
            remove_edge(rid, evidence_id, "wasDerivedFrom")
            affected.append({"result_id": rid, "action": "archived",
                             "reason": "single-member-ai" if origin == "ai" else "single-member-edited"})
        else:
            for m in target_members:
                remove_result_member(rid, m["id"])
            remove_edge(rid, evidence_id, "includes")
            remove_edge(rid, evidence_id, "supports")
            affected.append({"result_id": rid, "action": "removed_member",
                             "members_left": len(members) - len(target_members)})
    return {"affected": affected, "evidence_id": evidence_id}


def auto_interpret(result_id: str) -> Optional[str]:
    """A3: background interpretation generator. Reads the wrapping Result's first
    evidence member, finds the agent's narration adjacent to it in the chat (the
    Guide's existing thread context), and writes it back as the Result's
    interpretation with origin='ai'.

    Zero LLM tokens on the chat-pin path — the Guide ALREADY interpreted the
    figure in chat; we reuse that text. (For uploads with no chat context, we'd
    need an actual Haiku call — punted; the placeholder stays until the user
    edits, which is fine for upload-from-outside flows.)

    Returns the interpretation text, or None if nothing usable could be found.
    Idempotent: skips work if origin is already 'user' (user edited first)."""
    from core.graph.messages import get_messages
    from core.graph._schema import WORKSPACE_ID

    r = get_entity(result_id)
    if not r or r["type"] != "result":
        return None
    md = r.get("metadata") or {}
    if md.get("interpretation_origin") == "user":
        return None  # user beat us to it; do not overwrite
    members = md.get("members") or []
    member_ref = next((m.get("ref") for m in members if m.get("ref")), None)
    if not member_ref:
        return None
    ev = get_entity(member_ref)
    if not ev:
        return None
    art = ev.get("artifact_path")

    msgs = get_messages(WORKSPACE_ID, thread_id=md.get("thread_id")) if md.get("thread_id") else get_messages(WORKSPACE_ID)

    def asst_text(m: dict) -> str:
        if m.get("role") != "assistant":
            return ""
        return " ".join(b.get("text", "") for b in m.get("content", [])
                        if isinstance(b, dict) and b.get("type") == "text").strip()

    # Find the tool_result that produced this artifact, then take the assistant
    # text that follows. (Mirrors the existing suggest_interpretation endpoint.)
    prod_idx: Optional[int] = None
    for i, m in enumerate(msgs):
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_result" and art:
                try:
                    plots = (json.loads(blk["content"]) or {}).get("plots") or []
                    if any(p.get("url") == art for p in plots):
                        prod_idx = i
                except Exception:  # noqa: BLE001
                    pass
            elif blk.get("type") == "image" and art and blk.get("url") == art:
                prod_idx = i
    text = ""
    if prod_idx is not None:
        for j in range(prod_idx, min(prod_idx + 4, len(msgs))):
            t = asst_text(msgs[j])
            if t:
                text = t
                break
    if not text:                                # last-resort: most recent agent text in the thread
        for m in reversed(msgs):
            t = asst_text(m)
            if t:
                text = t
                break
    if not text:
        return None
    text = text[:400].strip()
    # Re-fetch in case user edited while we were searching.
    cur = get_entity(result_id)
    if not cur:
        return None
    cur_md = dict(cur.get("metadata") or {})
    if cur_md.get("interpretation_origin") == "user":
        return None
    cur_md["interpretation"] = text
    cur_md["interpretation_origin"] = "ai"
    update_entity(result_id, metadata=cur_md)
    return text


def promote_figure_to_result(
    figure_id: str,
    interpretation: str,
    title: Optional[str] = None,
) -> str:
    """Create a `result` entity that interprets a figure."""
    fig = get_entity(figure_id)
    if not fig:
        raise ValueError(f"figure {figure_id} not found")
    if fig["type"] != "figure":
        raise ValueError(f"can only promote figures (got {fig['type']})")

    auto_title = title or interpretation.strip().split("\n")[0][:80] or fig["title"]
    rid = create_entity(
        entity_type="result",
        title=auto_title,
        parent_entity_id=fig.get("parent_entity_id"),
        metadata={"interpretation": interpretation, "evidence_figure": figure_id},
    )
    add_edge(rid, figure_id, "supports", {"direction": "result-supported-by-figure"})
    add_edge(rid, figure_id, "wasDerivedFrom")
    return rid


def promote_results_to_finding(
    result_ids: list[str],
    text: str,
    title: Optional[str] = None,
) -> str:
    """Aggregate one or more results into a `finding`."""
    if not result_ids:
        raise ValueError("a finding requires at least one supporting result")
    results = [get_entity(rid) for rid in result_ids]
    if any(r is None for r in results):
        raise ValueError("one or more results not found")
    if any(r["type"] != "result" for r in results):  # type: ignore[index]
        raise ValueError("all sources must be result entities")

    auto_title = title or text.strip().split("\n")[0][:80]
    fid = create_entity(
        entity_type="finding",
        title=auto_title,
        metadata={"text": text, "supporting_results": result_ids},
    )
    for rid in result_ids:
        add_edge(fid, rid, "supports", {"direction": "finding-supported-by-result"})
        add_edge(fid, rid, "wasDerivedFrom")
    return fid


def add_result_to_finding(finding_id: str, result_id: str) -> dict:
    """Attach an additional result to an existing finding."""
    finding = get_entity(finding_id)
    if not finding or finding["type"] != "finding":
        raise ValueError("finding not found")
    result = get_entity(result_id)
    if not result or result["type"] != "result":
        raise ValueError("result not found")
    supporting = list((finding.get("metadata") or {}).get("supporting_results", []))
    if result_id not in supporting:
        supporting.append(result_id)
        meta = dict(finding.get("metadata") or {})
        meta["supporting_results"] = supporting
        update_entity(finding_id, metadata=meta)
        add_edge(finding_id, result_id, "supports", {"direction": "finding-supported-by-result"})
        add_edge(finding_id, result_id, "wasDerivedFrom")
    return get_entity(finding_id)  # type: ignore[return-value]


def remove_result_from_finding(finding_id: str, result_id: str) -> dict:
    finding = get_entity(finding_id)
    if not finding or finding["type"] != "finding":
        raise ValueError("finding not found")
    supporting = list((finding.get("metadata") or {}).get("supporting_results", []))
    if result_id in supporting:
        supporting.remove(result_id)
        meta = dict(finding.get("metadata") or {})
        meta["supporting_results"] = supporting
        update_entity(finding_id, metadata=meta)
        remove_edge(finding_id, result_id, "supports")
        remove_edge(finding_id, result_id, "wasDerivedFrom")
    return get_entity(finding_id)  # type: ignore[return-value]


def create_finding_from_draft(
    title: str,
    summary: str,
    evidence_ids: Optional[list[str]] = None,
    caveats: Optional[list[dict]] = None,
    status: str = "candidate",
) -> str:
    """Create a structured finding directly (selection-to-finding / M3).

    Evidence may be any entity (figure/table/result) — not just results — so
    a finding can be crystallized straight from chat before promotion.
    """
    evidence_ids = evidence_ids or []
    fid = create_entity(
        entity_type="finding",
        title=(title.strip()[:120] or "Untitled finding"),
        metadata={
            "text": summary, "summary": summary,
            "supporting_results": evidence_ids,
            "evidence": evidence_ids,
            "caveats": caveats or [],
            "maturity": status,
        },
    )
    for eid in evidence_ids:
        if get_entity(eid):
            add_edge(fid, eid, "supports", {"direction": "finding-supported-by-evidence"})
            add_edge(fid, eid, "wasDerivedFrom")
    return fid


def set_finding_fields(
    finding_id: str,
    summary: Optional[str] = None,
    caveats: Optional[list[dict]] = None,
    status: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Edit a finding's structured fields (M7 finding view)."""
    f = get_entity(finding_id)
    if not f or f["type"] != "finding":
        raise ValueError("finding not found")
    meta = dict(f.get("metadata") or {})
    if summary is not None:
        meta["summary"] = summary; meta["text"] = summary
    if caveats is not None:
        meta["caveats"] = caveats
    if status is not None:
        meta["maturity"] = status
    update_entity(finding_id, metadata=meta,
                  **({"title": title.strip()[:120]} if title else {}))
    return get_entity(finding_id)  # type: ignore[return-value]


def promote_findings_to_claim(
    finding_ids: list[str],
    text: str,
    title: Optional[str] = None,
) -> str:
    """Lift one or more findings into a publishable `claim`."""
    if not finding_ids:
        raise ValueError("a claim requires at least one supporting finding")
    findings = [get_entity(fid) for fid in finding_ids]
    if any(f is None for f in findings):
        raise ValueError("one or more findings not found")
    if any(f["type"] != "finding" for f in findings):  # type: ignore[index]
        raise ValueError("all sources must be finding entities")

    auto_title = title or text.strip().split("\n")[0][:80]
    cid = create_entity(
        entity_type="claim",
        title=auto_title,
        metadata={"text": text, "supporting_findings": finding_ids},
    )
    for fid in finding_ids:
        add_edge(cid, fid, "supports", {"direction": "claim-supported-by-finding"})
        add_edge(cid, fid, "wasDerivedFrom")
    return cid
