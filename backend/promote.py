"""
Promotion flows: figure → result → finding → claim.

Each promotion creates a new higher-level entity and edges it to its
source(s) with `supports` (for evidence) and `wasDerivedFrom` (so the
provenance walk works the same as for run-generated artifacts).
"""
from __future__ import annotations
from typing import Optional

from db import create_entity, get_entity, add_edge


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
