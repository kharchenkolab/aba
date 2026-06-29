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
import logging
import re
from pathlib import Path
from typing import Optional

from core.graph._schema import WORKSPACE_ID
from core.graph.edges import add_edge
from core.graph.entities import create_entity, get_entity, update_entity

_log = logging.getLogger(__name__)


def _ensure_analysis(focused_entity_id: str, analysis_ctx: dict,
                     thread_id: Optional[str] = None) -> str:
    """
    Find or create the `analysis` entity (= Run) that new artifacts attach to.

    Resolution order:
    1. The id already cached for this turn (analysis_ctx).
    2. The thread's OPEN Run, if one exists — so a planned, multi-turn pipeline
       groups under ONE Run (runs.open_run / close_run) instead of a fresh
       per-turn analysis each time.
    3. Otherwise a lazily-created per-turn `analysis` (the small-scale fallback).
       When focused on a leaf artifact, group under its parent analysis.

    `analysis_ctx` is shared across tool calls within one Guide turn.
    """
    if analysis_ctx.get("analysis_id"):
        return analysis_ctx["analysis_id"]

    if thread_id:
        from content.bio.lifecycle.runs import active_run_id
        rid = active_run_id(thread_id)
        if rid:
            analysis_ctx["analysis_id"] = rid
            return rid

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

    # Tag it open + thread-scoped so the NEXT turn's _ensure_analysis (via
    # active_run_id) REUSES it instead of minting a fresh anonymous "Analysis"
    # every turn — otherwise pre-plan ad-hoc work piles up N analyses. A
    # present_plan/open_run later rotates this ambient one out (kept if it has
    # artifacts, discarded if empty).
    # `ambient`: this is the catch-all analysis for ad-hoc, pre-plan work — it
    # exists only to parent stray outputs, so it's HIDDEN from the Runs UI (a
    # named Run comes from open_run / an approved plan). Still structurally real.
    from core.graph.derivation import manual, SYSTEM_ACTOR
    aid = create_entity(
        entity_type="analysis",
        title=title,
        parent_entity_id=parent,
        derivation=manual(), actor=SYSTEM_ACTOR,   # Phase 2B: ambient/system run
        metadata={"thread_id": thread_id, "run_state": "open", "origin": "internal",
                  "ambient": True} if thread_id else {"ambient": True},
    )
    # The ambient analysis's output dir IS the shared thread scratch dir (where
    # run_python/run_r write when no named run is open) — so its outputs are
    # browsable under it, mirroring a named run's own dir.
    if thread_id:
        try:
            from core.data.workspace import scratch_dir
            from core import projects
            d = scratch_dir(projects.current() or "default", f"thread-{thread_id}")
            update_entity(aid, artifact_path=str(d))
        except Exception:  # noqa: BLE001
            pass
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
    # Reproducibility tag (kernels.md §8.1): a "stateless" artifact's producing_code
    # reproduces it standalone; a "session" artifact (built in a persistent kernel)
    # is reproduced by replaying this thread's ordered cells.
    _mode = result_obj.get("execution_mode") if isinstance(result_obj, dict) else None
    res_meta["execution_mode"] = _mode or "stateless"
    res_meta["reproducible"] = "self_contained" if res_meta["execution_mode"] == "stateless" else "session"

    # Capture every executed cell onto the thread's open Run (if any), so the
    # Run is the recompute unit — not just cells that happened to emit a figure.
    if tool_name in ("run_python", "run_r", "run_nextflow") and thread_id:
        from content.bio.lifecycle.runs import active_run_id, append_run_code
        # Prefer the Run captured for THIS result (analysis_ctx) over the thread's
        # currently-open Run: a long background job may complete after the agent
        # has moved on to a different Run, and its code/outputs must still land on
        # the Run it was submitted under. Interactive calls leave analysis_ctx
        # empty here and fall back to active_run_id (unchanged behavior).
        _rid = analysis_ctx.get("analysis_id") or active_run_id(thread_id)
        if _rid:
            append_run_code(_rid, tool_input.get("code", "") if isinstance(tool_input, dict) else "")

    # ──── Option B / Phase 5 cutover ────────────────────────────────────
    # `register_artifacts_from_tool_result` USED to mint a figure entity
    # per harvested PNG + a table entity per harvested CSV, even when the
    # user never pinned anything. That created thousands of shadow
    # entities cluttering the entity table.
    #
    # Post-cutover: artifacts live in the exec record's `produced[]` only.
    # Entities are minted only when the user (or an explicit auto-pin
    # path like plan-Go declared finals — Phase 6) pins them, via
    # `content/bio/lifecycle/artifacts.pin_artifact`. The chat shows
    # them inline via the ArtifactPin button (Phase 3); RunView /
    # Files tree augment each output with `artifact_id` (Phase 4) so a
    # pin click goes to /api/artifacts/.../pin.
    #
    # What this function still does: ensures the Run (analysis) entity
    # exists, refreshes its output manifest below, and writes a
    # `analysis --used--> focused_entity` edge when the cell was focused
    # on an upstream entity (provenance the user expects to navigate).
    plots = result_obj.get("plots") if isinstance(result_obj, dict) else None
    tables = result_obj.get("tables") if isinstance(result_obj, dict) else None
    if tool_name in ("run_python", "run_r", "run_nextflow") and (plots or tables):
        analysis_id = _ensure_analysis(focused_entity_id or WORKSPACE_ID, analysis_ctx, thread_id)
        focused = focused_entity_id or WORKSPACE_ID
        if focused != WORKSPACE_ID:
            try:
                add_edge(analysis_id, focused, "used")
            except Exception as e:  # noqa: BLE001 — best-effort
                _log.debug("analysis -> used edge failed: %s", e)
        # Option B / Phase 5: backfill the exec record's run_id to point
        # at the ambient analysis (or whatever Run _ensure_analysis
        # resolved to). Without this, an exec written BEFORE the ambient
        # analysis was created has run_id=NULL, and artifacts_for_run
        # can't find its artifacts. The run-from-an-open-Run path
        # already has the right run_id at exec-write time; this catches
        # the ambient-lazy-create case.
        exec_id_ptr = result_obj.get("exec_id") if isinstance(result_obj, dict) else None
        if exec_id_ptr:
            try:
                from core.graph.exec_records import attach_to_run
                attach_to_run(exec_id_ptr, analysis_id)
            except Exception as e:  # noqa: BLE001
                _log.warning("exec_records.attach_to_run failed: %s", e)

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

    # Refresh the active Run's output manifest (figures/tables/files in its dir)
    # after EVERY code cell — even one that wrote only a .rds and emitted no
    # figure — so the Run view always reflects what the pipeline produced. Map
    # harvested PNG urls (served from /artifacts) as figure thumbnails.
    if tool_name in ("run_python", "run_r", "run_nextflow") and thread_id:
        try:
            from content.bio.lifecycle.runs import active_run_id, refresh_output_manifest
            # Same as above: refresh the Run this result belongs to, not just
            # whatever Run is open now (matters for background-job completion).
            _rid = analysis_ctx.get("analysis_id") or active_run_id(thread_id)
            if _rid:
                _plots = (result_obj.get("plots") or []) if isinstance(result_obj, dict) else []
                _by_name = {p.get("original_name"): p.get("url") for p in _plots
                            if p.get("original_name") and p.get("url")}
                # The harvester's authoritative output names (rel to the Run dir):
                # pass them so a just-finished Slurm job's outputs attach even if
                # the login-node dir listing hasn't caught up (NFS — see
                # refresh_output_manifest).
                _names = [a.get("original_name") for grp in ("plots", "tables", "files")
                          for a in (result_obj.get(grp) or []) if a.get("original_name")] \
                    if isinstance(result_obj, dict) else []
                refresh_output_manifest(_rid, plot_urls_by_name=_by_name, ensure_names=_names)
        except Exception:  # noqa: BLE001 — manifest is best-effort cosmetic
            pass
    return refreshed


_TITLE_PATTERNS = [
    re.compile(r"""\.set_title\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bplt\.title\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\.suptitle\(\s*['"]([^'"]+)['"]"""),
    # R / ggplot title calls (Seurat DimPlot/VlnPlot etc. return ggplots)
    re.compile(r"""\bggtitle\(\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\blabs\([^)]*title\s*=\s*['"]([^'"]+)['"]"""),
    re.compile(r"""\bplot_annotation\([^)]*title\s*=\s*['"]([^'"]+)['"]"""),
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


def _meaningful_title(s: str) -> bool:
    """A title must carry real words — reject separator / progress-bar noise like
    '====================' or '----' that pagoda2-style banner comments emit
    (which otherwise becomes the figure name). Needs ≥2 alphanumerics and not be
    mostly punctuation."""
    s = (s or "").strip()
    alnum = sum(ch.isalnum() for ch in s)
    return alnum >= 2 and alnum >= len(s) * 0.4


def _figure_title(code: str, original_name: str, idx: int, multi: bool) -> str:
    """Display title for a produced figure. The code-derived title is the same
    for every plot in one run (it scans the whole block), so when a run emits
    several plots we disambiguate — by the saved filename stem if it's
    meaningful, otherwise a 1-based index — so siblings don't collide."""
    # Precedence: an explicit in-code plot title (most reliable) → a MEANINGFUL
    # saved filename (the agent names plots descriptively: umap_clusters.png) →
    # the first code comment (often just a step label like "Run UMAP", which makes
    # a poor figure title) → the raw stem.
    stem = Path(original_name).stem
    stem_disp = stem.replace("_", " ").strip()
    # Reject auto-generated names — R's default device (Rplot001) and generic
    # matplotlib/harvest stems (figure3, plot1, out) — so a descriptive comment
    # wins over device junk; a hand-named file (umap_clusters) still wins.
    auto = bool(re.match(r"^(rplot|rplots|figure|fig|plot|out|output|image|img|result|untitled)\d*$", stem.lower()))
    stem_ok = bool(stem) and not auto and stem.lower() not in _GENERIC_STEMS and _meaningful_title(stem_disp)
    base = (_explicit_title(code)
            or (stem_disp if stem_ok else None)
            or _title_from_code(code)
            or stem)
    if not multi:
        return base
    stem = Path(original_name).stem
    if stem and stem.lower() not in _GENERIC_STEMS and stem.lower() != base.lower():
        return f"{base} — {stem}"
    return f"{base} ({idx + 1})"


def _explicit_title(code: str) -> Optional[str]:
    """An explicit in-code plot title — matplotlib (set_title/plt.title/suptitle)
    or R/ggplot (ggtitle/labs(title=)/plot_annotation). The most reliable signal
    when present; describes the FIGURE, not the step."""
    if not code:
        return None
    for pat in _TITLE_PATTERNS:
        m = pat.search(code)
        if m and _meaningful_title(m.group(1)):
            return m.group(1).strip()[:80]
    return None


def _title_from_code(code: str) -> Optional[str]:
    """
    Derive a meaningful figure title from the producing code:
    1) An explicit plot title call (matplotlib or ggplot).
    2) Otherwise the first non-generic top-level comment.
    """
    if not code:
        return None
    explicit = _explicit_title(code)
    if explicit:
        return explicit
    for line in code.splitlines():
        s = line.strip()
        if not s.startswith("# ") or s.startswith("# !"):
            continue
        body = s[2:].strip()
        if not body or body.lower() in _GENERIC_COMMENTS or not _meaningful_title(body):
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
