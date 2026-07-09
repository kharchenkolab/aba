"""Admin / diagnostics routes — server-wide MCP + tool telemetry + one-shot
cleanup. Extracted from main.py (Item 2A.3). Domain-neutral (core.runtime only).

(`/api/admin/backfill-tool-result-thread` remains in main.py near the entities
section for now; the three GETs + purge here are the contiguous admin block.)
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/admin/mcp")
def admin_mcp_status():
    """Per-server health, tool counts, last error — drawer can show
    'MCP: 2/3 servers up'."""
    from core.runtime.mcp import status
    return status()


@router.get("/api/admin/selfcheck")
def admin_selfcheck():
    """Startup self-check results (core/runtime/selfcheck) — the diagnostics drawer
    shows outstanding warnings (e.g. 'ENVS_DIR node-local under Slurm'). `re_run`
    is not offered here: checks run at boot; a config change needs a restart."""
    from core.runtime import selfcheck
    return selfcheck.summary()


@router.get("/api/admin/tool_stats")
def admin_tool_stats(days: int = 30, project_id: str | None = None):
    """Tool-catalog usage observability (tool_use a1). `tools`: per-tool invocation
    aggregates (count, ok/error/rejected/deferred, avg+max duration). `generations`:
    per-LLM-generation round-trips/turn, parallelism (tool_uses per generation), and
    prompt-cache effectiveness (cache_read vs cache_write). Telemetry is per-project —
    pass project_id to read a specific project's DB; window defaults to 30 days."""
    import contextlib
    from core import projects
    from core.runtime.tool_telemetry import stats, generation_stats
    cm = projects.bind(project_id) if project_id else contextlib.nullcontext()
    with cm:
        return {"tools": stats(days=days), "generations": generation_stats(days=days)}


@router.get("/api/admin/tool_invocations")
def admin_tool_invocations(limit: int = 50, tool_name: str | None = None):
    """Raw recent invocations for debugging."""
    from core.runtime.tool_telemetry import recent_invocations
    return recent_invocations(limit=limit, tool_name=tool_name)


@router.post("/api/admin/purge_orphan_fills")
def admin_purge_orphan_fills():
    """One-shot cleanup for the buggy-reaper duplication: removes user
    messages whose content is entirely orphan-fill tool_results. Safe to
    call repeatedly (no-op on a clean DB). Uses the backend's own
    connection so it doesn't violate the never-touch-live-DB rule."""
    from core.runtime.checkpoint import purge_orphan_fill_messages
    n = purge_orphan_fill_messages()
    return {"touched": n}
