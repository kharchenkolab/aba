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
import re
from pathlib import Path
from typing import Optional

from core.graph._schema import WORKSPACE_ID
from core.graph.edges import add_edge
from core.graph.entities import create_entity, get_entity


def _ensure_analysis(focused_entity_id: str, analysis_ctx: dict) -> str:
    """
    Lazily create (and remember) an `analysis` entity for this turn.

    If the user is focused on an existing figure/table/result, group new
    artifacts under THAT entity's parent analysis (sibling relationship)
    rather than nesting "Analysis of mt_fraction histogram" under the
    figure itself.

    `analysis_ctx` is shared across tool calls within one Guide turn.
    """
    if analysis_ctx.get("analysis_id"):
        return analysis_ctx["analysis_id"]

    focused = focused_entity_id or WORKSPACE_ID
    parent = focused
    title = "Analysis"

    if focused != WORKSPACE_ID:
        focused_ent = get_entity(focused)
        if focused_ent:
            # When focused on a leaf artifact, prefer its parent analysis.
            if focused_ent["type"] in ("figure", "table", "result", "finding"):
                if focused_ent["parent_entity_id"]:
                    parent = focused_ent["parent_entity_id"]
                    title = f"Follow-up on {focused_ent['title']}"
                else:
                    parent = focused
                    title = f"Analysis of {focused_ent['title']}"
            else:
                title = f"Analysis of {focused_ent['title']}"

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
    thread_id: Optional[str] = None,
) -> list[dict]:
    """
    Inspect a tool result; register any artifacts as entities.
    Returns the new entity records (full row dicts, ready to send via SSE).

    `thread_id` (v3) tags each Result with its home thread + origin=internal,
    stored in metadata so the per-thread pinned shelf can filter on it.
    """
    new_records: list[dict] = []
    res_meta = {"thread_id": thread_id, "origin": "internal"} if thread_id else {"origin": "internal"}

    plots = result_obj.get("plots") if isinstance(result_obj, dict) else None
    if tool_name == "run_python" and plots:
        analysis_id = _ensure_analysis(focused_entity_id or WORKSPACE_ID, analysis_ctx)
        producing_code = tool_input.get("code", "") if isinstance(tool_input, dict) else ""
        multi = len(plots) > 1
        for i, p in enumerate(plots):
            url = p.get("url")
            original_name = p.get("original_name") or "figure.png"
            title = _figure_title(producing_code, original_name, i, multi)

            # Every plot is its own figure. We deliberately do NOT auto-supersede
            # by title — that conflated distinct plots (and silently retired
            # pinned ones). Version chains (wasRevisionOf) are a future, EXPLICIT
            # concern (lineage model), not inferred from a matching title.
            eid = create_entity(
                entity_type="figure",
                title=title,
                artifact_path=url,
                producing_code=producing_code,
                parent_entity_id=analysis_id,
                metadata={"original_name": original_name, **res_meta},
            )
            # PROV-O edges: figure wasGeneratedBy the analysis;
            # the analysis used the focused entity (if any).
            add_edge(eid, analysis_id, "wasGeneratedBy")
            focused = focused_entity_id or WORKSPACE_ID
            if focused != WORKSPACE_ID:
                add_edge(analysis_id, focused, "used")
                add_edge(eid, focused, "wasDerivedFrom")
            rec = get_entity(eid)
            if rec:
                new_records.append(rec)

    # Output CSVs → table entities.
    tables = result_obj.get("tables") if isinstance(result_obj, dict) else None
    if tool_name == "run_python" and tables:
        analysis_id = _ensure_analysis(focused_entity_id or WORKSPACE_ID, analysis_ctx)
        producing_code = tool_input.get("code", "") if isinstance(tool_input, dict) else ""
        for t in tables:
            original_name = t.get("original_name") or "table.csv"
            title = _title_from_code(producing_code) or original_name
            eid = create_entity(
                entity_type="table",
                title=title,
                artifact_path=t.get("url"),
                producing_code=producing_code,
                parent_entity_id=analysis_id,
                metadata={"original_name": original_name, **res_meta},
            )
            add_edge(eid, analysis_id, "wasGeneratedBy")
            focused = focused_entity_id or WORKSPACE_ID
            if focused != WORKSPACE_ID:
                add_edge(eid, focused, "wasDerivedFrom")
            rec = get_entity(eid)
            if rec:
                new_records.append(rec)

    # F3: persist display_path for everything we just registered. Re-fetch
    # afterwards so the new value flows back to the caller (and the SSE
    # entity_registered event the UI consumes).
    from content.bio.graph.display import recompute_display_path
    refreshed = []
    for rec in new_records:
        recompute_display_path(rec["id"])
        latest = get_entity(rec["id"])
        if latest:
            refreshed.append(latest)
    return refreshed


_TITLE_PATTERNS = [
    re.compile(r"""\.set_title\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bplt\.title\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\.suptitle\(\s*['"]([^'"]+)['"]"""),
]

# Comments to skip when nothing else turns up — generic action verbs that
# describe what the agent is doing, not what the artifact is about.
_GENERIC_COMMENTS = {
    "read the data", "read data", "load the data", "load data",
    "import libraries", "imports", "imports and setup", "setup",
    "make a plot", "make plot", "plot", "plot the data", "plot data",
    "create a plot", "create plot", "create histogram", "create the histogram",
    "make figure", "make a figure", "make a histogram",
}

_GENERIC_STEMS = {"out", "output", "figure", "fig", "plot", "image", "img", "result"}


def _figure_title(code: str, original_name: str, idx: int, multi: bool) -> str:
    """Display title for a produced figure. The code-derived title is the same
    for every plot in one run (it scans the whole block), so when a run emits
    several plots we disambiguate — by the saved filename stem if it's
    meaningful, otherwise a 1-based index — so siblings don't collide."""
    base = _title_from_code(code) or Path(original_name).stem
    if not multi:
        return base
    stem = Path(original_name).stem
    if stem and stem.lower() not in _GENERIC_STEMS and stem.lower() != base.lower():
        return f"{base} — {stem}"
    return f"{base} ({idx + 1})"


def _title_from_code(code: str) -> Optional[str]:
    """
    Derive a meaningful figure title from the producing code:
    1) Look for matplotlib title calls (set_title / plt.title / suptitle).
    2) Otherwise the first non-generic top-level comment.
    """
    if not code:
        return None
    for pat in _TITLE_PATTERNS:
        m = pat.search(code)
        if m:
            return m.group(1).strip()[:80]
    for line in code.splitlines():
        s = line.strip()
        if not s.startswith("# ") or s.startswith("# !"):
            continue
        body = s[2:].strip()
        if not body or body.lower() in _GENERIC_COMMENTS:
            continue
        return body[:80]
    return None


# ---------- Hook handlers ----------
# Pass D: bio registers itself as a post-tool hook so guide.py doesn't have
# to know about artifact registration.

from core.hooks.dispatcher import register as _register_hook


def _on_post_tool_register_artifacts(ctx: dict) -> None:
    """ctx fields: tool_name, tool_input, result_obj, focus_entity_id,
    analysis_ctx, thread_id. Appends to ctx['new_entities']."""
    new = register_artifacts_from_tool_result(
        tool_name=ctx["tool_name"],
        tool_input=ctx["tool_input"],
        result_obj=ctx["result_obj"],
        focused_entity_id=ctx["focus_entity_id"],
        analysis_ctx=ctx["analysis_ctx"],
        thread_id=ctx.get("thread_id"),
    )
    ctx.setdefault("new_entities", []).extend(new)


_register_hook("on_post_tool", _on_post_tool_register_artifacts, priority=10)


def _on_job_complete_register_artifacts(ctx: dict) -> None:
    """Background-job completion: register the produced artifacts.
    Same shape as on_post_tool — the same handler logic works."""
    _on_post_tool_register_artifacts(ctx)


_register_hook("on_job_complete", _on_job_complete_register_artifacts, priority=10)
