"""Bio's content-provided services for the ``core/services`` seam — so core can
ask bio for values it needs (code-language sniffing for exec backfill; the host
tool catalog for the recovery report) without ``core/`` importing ``content/``.

Registered at import (pulled in by ``content/bio/__init__.py``). The actual bio
imports are deferred to call time so this module is import-order-safe.
"""
from __future__ import annotations

from core.services import register_service


def _language_sniffer(code: str) -> str:
    """R signals beat python signals; default to python on tie. (Wraps the
    scenarios heuristic; imported lazily.)"""
    from content.bio.lifecycle.scenarios import _detect_language
    return _detect_language(code)


def _host_tool_names():
    """The host's agent-visible tool names — the live MCP catalog if the gateway
    has booted, else a scan of aba_core's tool modules, plus the run_* workhorses.
    Returns a set, or None if nothing could be enumerated."""
    names: set[str] = set()
    try:
        from content.bio.tools import TOOL_SCHEMAS
        names.update(t["name"] for t in TOOL_SCHEMAS if isinstance(t, dict) and t.get("name"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import re
        from pathlib import Path
        tools_dir = Path(__file__).resolve().parent / "mcp_servers/aba_core/tools"
        for f in tools_dir.glob("*.py"):
            for line in f.read_text().splitlines():
                m = re.match(r"\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line)
                if m and not m.group(1).startswith("_"):
                    names.add(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    names.update({"run_python", "run_r"})
    return names or None


def _plan_orientation_preamble(project_id: str, thread_id: str) -> str:
    """Workspace-orientation block for a just-presented plan: canonical dataset
    paths + prior-run files reachable from the new Run's cwd, so the agent uses
    real paths on its first run_python instead of guessing. Composes bio's own
    run-workspace privates — the orchestrator (guide) asks for this through the
    ``core/services`` seam instead of importing them (modularity_audit3 Item 1)."""
    from content.bio.tools.run_exec import _prior_run_files_preamble, _run_scratch_cwd
    from content.bio.lifecycle.runs import active_run_id
    pid, tid = str(project_id), str(thread_id)
    return _prior_run_files_preamble(
        pid, tid,
        current_run_id=active_run_id(tid),
        cwd=_run_scratch_cwd(pid, tid),
    )


def _result_cascade_members(result_id: str) -> set:
    """Containment set for `cascade=members` on a Result delete (moved from main.py,
    Item 2A.4): every figure/table/cell member referenced from metadata.members, plus
    each member's full revision chain (active + superseded). The Result id itself is
    NOT included — it's deleted separately. This is bio-domain knowledge (Result/figure/
    revision semantics + the "figure"/"table" types), so the platform delete route asks
    for it through the core/services seam instead of naming bio types itself.

    Why include superseded revisions: deleting a Result should take its whole history
    with it (superseded revisions are figure entities from make_revision, referenced
    nowhere visible; left behind they look like a leak)."""
    from content.bio.graph.figure_history import figure_history
    from core.graph.entities import get_entity
    out: set = set()
    r = get_entity(result_id)
    if not r:
        return out
    members = (r.get("metadata") or {}).get("members") or []
    member_ids = [m.get("ref") for m in members if isinstance(m, dict) and m.get("ref")]
    for mid in member_ids:
        m = get_entity(mid)
        if not m:
            continue
        out.add(mid)
        # Expand revision chains for figure/table members. Cells don't currently form
        # revision chains via wasRevisionOf, but figure_history is safe on any type.
        if m.get("type") in ("figure", "table"):
            try:
                chain = figure_history(mid, include_superseded=True)
                for e in chain:
                    if e and e.get("id"):
                        out.add(e["id"])
            except Exception:  # noqa: BLE001 — chain walk is best-effort
                pass
    return out


register_service("language_sniffer", _language_sniffer)
register_service("host_tool_names", _host_tool_names)
register_service("plan_orientation_preamble", _plan_orientation_preamble)
register_service("result_cascade_members", _result_cascade_members)
