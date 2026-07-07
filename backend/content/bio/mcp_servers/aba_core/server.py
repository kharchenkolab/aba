"""FastMCP server factory for aba_core.

A zero-arg factory function that returns a fresh `FastMCP` instance.
The gateway calls this on every (re)connect so a crashed handle gets a
clean rebuild. Each sub-phase grows the registration block at the
bottom; 6.A is the empty scaffold.

Why a factory rather than a module-level singleton?
- `mcp.server.fastmcp.FastMCP` holds anyio state internally; reusing
  one across reconnects after an exception is fragile.
- A factory matches the stdio-subprocess pattern (every reconnect
  respawns the process); same restart-on-crash semantics carry over.
- Tests can construct a server in isolation without globals.
"""
from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from .tools.simple import register_simple_tools
from .tools.ctx_read import register_ctx_read_tools
from .tools.curation import register_curation_tools
from .tools.discovery import register_discovery_tools
from .tools.file_io import register_file_io_tools
from .tools.plan_etc import register_plan_etc_tools
from .tools.run_exec import register_run_exec_tools
from .tools.revisions import register_revision_tools
from .tools.cells import register_cell_tools
from .tools.entity_ops import register_entity_ops_tools
from .tools.jobs import register_jobs_tools
from .tools.feedback import register_feedback_tools
from .tools.viewers import register_viewer_tools


def make_server() -> FastMCP:
    """Build a fresh aba_core MCP server.

    Tool registration happens here (or via per-cluster
    `register_<cluster>` helpers). 6.A intentionally registers zero
    tools — the goal is to prove the wiring: server starts, gateway
    connects via memory transport, `list_tools()` reports 0 tools."""
    mcp = FastMCP(
        name="aba_core",
        instructions=(
            "In-process MCP server hosting ABA bio's tool catalogue. "
            "Phase 6 of arch3.md — see misc/phase6_mcp_wrapping.md. "
            "All tools are exposed via the same gateway channel as "
            "external stdio servers (e.g. lakefs). Tools requiring "
            "runtime context (cancel_token, kernel session) read it "
            "from a contextvar set by the dispatcher before the call "
            "lands here."
        ),
    )

    # Per-cluster registrations — keeping them as explicit calls makes
    # the migration progress legible at a glance.
    register_simple_tools(mcp)      # 6.B: list_capabilities, read_memory, search_pypi
    register_ctx_read_tools(mcp)    # 6.C: Skill, read_skill, list_entities, get_provenance,
                                    #      get_dependents, read_capability, read_csv_info
    register_curation_tools(mcp)    # 6.D: pin/promote/findings/claims/datasets/runs/refs
    register_discovery_tools(mcp)   # 6.E: search_*/inspect_package/ensure/propose/fetch_*
    register_file_io_tools(mcp)     # 6.F: list_data_files, inspect_upload, write/edit/read_file
    register_plan_etc_tools(mcp)    # 6.G: present_plan, ask_clarification, create_scenario,
                                    #      write_memory, restart_kernel, run_nextflow
    register_run_exec_tools(mcp)    # 6.H: run_python, run_r
    register_revision_tools(mcp)    # Stage 5 of misc/exec_records_and_versioning.md:
                                    #      make_revision, reproduce_from_exec
    register_cell_tools(mcp)        # Stage 6 of misc/exec_records_and_versioning.md:
                                    #      pin_cell
    register_entity_ops_tools(mcp)  # Entity-mgmt refactor (2026-06-08):
                                    #      read_entity (generic YAML-driven reader)
    register_feedback_tools(mcp)    # misc/feedback.md: build_bug_report (mailto: assembler)
    register_viewer_tools(mcp)      # misc/pagoda3_integration.md: open_viewer (external-viewer launch link)
    register_jobs_tools(mcp)        # J-1/J-2 (2026-06-08): get_job_status,
                                    #      cancel_job — let the agent answer
                                    #      "is it still running?" without
                                    #      deflecting to the UI Queues panel.

    # tool_library read-flip (opt-in via ABA_TOOL_LIB): the in-kernel `aba`
    # library (aba.find / aba.get, injected into run_python) replaces the entity
    # read TOOLS — so demote them from the catalog when the flag is on. Validated
    # safe by the Phase-1 forced arm (opus + haiku adopt aba cleanly, zero
    # reinvention, no quality regression). Seam-clean: bio names its own tools.
    # ABA_TOOL_LIB = the in-kernel aba library REPLACES the tools it covers — the WHOLE
    # entity contact plane (reads + writes + curation) is now aba.*:
    #   reads:   list_entities/read_entity → aba.find/get
    #   writes:  register_dataset→aba.register_dataset, annotate_entity/update_entity_fields
    #            →aba.update, add_to_dataset→aba.relate
    #   curation (content-provided via the aba_intent service): promote_to_result→aba.promote,
    #            create_finding→aba.finding, create_claim→aba.claim
    # A COHERENT surface: everything you do TO entities is aba.*, scripted in run_python.
    # (Specialized subsystems — revisions/provenance/search/capabilities/jobs — remain tools
    # for now; they're the next tranche.) Only the boundary tools that SUSPEND the loop
    # (present_plan/ask_clarification) or INJECT context (view_artifact/vision) always stay.
    _demote = []
    if os.environ.get("ABA_TOOL_LIB"):
        _demote += ["list_entities", "read_entity",
                    "register_dataset", "annotate_entity", "update_entity_fields", "add_to_dataset",
                    "promote_to_result", "create_finding", "create_claim"]
    if _demote:
        tm = mcp._tool_manager
        for _t in _demote:
            try:
                tm.remove_tool(_t)
            except Exception:
                tm._tools.pop(_t, None)

    return mcp
