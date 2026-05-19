"""
Auto-registration of artifacts produced by the Guide's tool calls.

When the Guide runs a tool whose output includes artifacts (figures, tables),
each artifact is registered as an entity in the analysis graph, edged back to
a lazily-created `analysis` entity for the current turn.

This is the Phase-1 implementation:
- run_python's `plots` list → figure entities
- (Tables / CSV outputs left for Phase 2/3 once the agent starts producing them
  intentionally.)
"""
from __future__ import annotations
from typing import Optional

from db import create_entity, get_entity, WORKSPACE_ID


def _ensure_analysis(focused_entity_id: str, analysis_ctx: dict) -> str:
    """
    Lazily create (and remember) an `analysis` entity for this turn.
    `analysis_ctx` is a dict shared across calls within one Guide turn.
    """
    if analysis_ctx.get("analysis_id"):
        return analysis_ctx["analysis_id"]

    parent = focused_entity_id or WORKSPACE_ID
    title = "Analysis"
    if parent != WORKSPACE_ID:
        e = get_entity(parent)
        if e:
            title = f"Analysis of {e['title']}"
    aid = create_entity(
        entity_type="analysis",
        title=title,
        parent_entity_id=parent,
    )
    analysis_ctx["analysis_id"] = aid
    return aid


def register_artifacts_from_tool_result(
    *,
    tool_name: str,
    tool_input: dict,
    result_obj: dict,
    focused_entity_id: Optional[str],
    analysis_ctx: dict,
) -> list[dict]:
    """
    Inspect a tool result; register any artifacts as entities.
    Returns the new entity records (full row dicts, ready to send via SSE).
    """
    new_records: list[dict] = []

    plots = result_obj.get("plots") if isinstance(result_obj, dict) else None
    if tool_name == "run_python" and plots:
        analysis_id = _ensure_analysis(focused_entity_id or WORKSPACE_ID, analysis_ctx)
        producing_code = tool_input.get("code", "") if isinstance(tool_input, dict) else ""
        for p in plots:
            url = p.get("url")
            original_name = p.get("original_name") or "figure.png"
            title = _title_from_code(producing_code) or original_name
            eid = create_entity(
                entity_type="figure",
                title=title,
                artifact_path=url,
                producing_code=producing_code,
                parent_entity_id=analysis_id,
                metadata={"original_name": original_name},
            )
            rec = get_entity(eid)
            if rec:
                new_records.append(rec)

    return new_records


def _title_from_code(code: str) -> Optional[str]:
    """First non-shebang comment of the producing code, if any."""
    for line in (code or "").splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("# !"):
            return s[2:].strip()[:80]
    return None
