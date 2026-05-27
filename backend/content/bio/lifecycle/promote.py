"""
Promotion flows: figure → result → finding → claim.

Each promotion creates a new higher-level entity and edges it to its
source(s) with `supports` (for evidence) and `wasDerivedFrom` (so the
provenance walk works the same as for run-generated artifacts).
"""
from __future__ import annotations
from typing import Optional

from core.graph.edges import add_edge, remove_edge
from core.graph.entities import create_entity, get_entity, update_entity


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
