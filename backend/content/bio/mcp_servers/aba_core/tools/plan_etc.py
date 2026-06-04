"""Phase 6.G — plan / scenario / write-memory / runtime-control cluster.

  - present_plan, ask_clarification: agent-side gestures that guide.py
    intercepts BEFORE the dispatcher runs (the bio impl is a stub
    returning a placeholder status). We register them on aba_core so
    the schema lives here too — post 6.I they're the only way the
    agent learns these tools exist.
  - create_scenario: variant-figure registration
  - write_memory: persist a note across sessions
  - restart_kernel: clear the python/r kernel state in this thread
  - run_nextflow: launch an nf-core pipeline (long-running)

Mix of pure (create_scenario, write_memory) and ctx-using
(restart_kernel for sess lookup, run_nextflow for cancel_token).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_plan_etc_tools(mcp: FastMCP) -> None:
    """Register the 6 plan/scenario/write-memory/runtime tools on `mcp`."""

    @mcp.tool()
    def create_scenario(baseline_id: str, description: str,
                        code: str) -> dict:
        """Register a scenario VARIANT of a baseline figure. The agent
        runs `code` (a tweak of the baseline's producing_code) and the
        result is recorded as a variant with `variantOf` provenance."""
        from content.bio.tools import create_scenario as _impl
        return _impl({"baseline_id": baseline_id, "description": description,
                      "code": code})

    @mcp.tool()
    def present_plan(steps: list,
                     title: str | None = None,
                     summary: str | None = None,
                     assumptions: list[str] | None = None,
                     rationale: str | None = None) -> dict:
        """Present a stepwise plan to the user for approval. The
        framework intercepts this and pauses the turn — the user
        approves with `go` or asks for adjustments."""
        from content.bio.tools import present_plan as _impl
        return _impl({"steps": steps, "title": title, "summary": summary,
                      "assumptions": assumptions, "rationale": rationale})

    @mcp.tool()
    def ask_clarification(question: str) -> dict:
        """Pause the turn on a one-line question to the user. Lighter
        weight than present_plan — no plan entity, no validator."""
        from content.bio.tools import ask_clarification as _impl
        return _impl({"question": question})

    @mcp.tool()
    def write_memory(name: str, type: str, body: str,
                     description: str | None = None) -> dict:
        """Save a note (memory file) that's restorable in future
        sessions via read_memory. Use sparingly — for orientation
        ('where datasets live', 'this script's quirks'), not as a
        substitute for citing fresh sources."""
        from content.bio.tools import write_memory_tool
        return write_memory_tool(
            {"name": name, "type": type, "body": body,
             "description": description})

    @mcp.tool()
    def restart_kernel(aba_ctx_id: str | None = None) -> dict:
        """Clear the persistent python/r kernel state for this thread.
        Variables, imports, plots — all gone. Use when state has
        drifted or a heavy library is causing problems."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import restart_kernel_tool
        return restart_kernel_tool({}, peek_ctx(aba_ctx_id))

    @mcp.tool()
    def run_nextflow(pipeline: str,
                     revision: str | None = None,
                     profile: str | None = None,
                     params: dict | None = None,
                     outdir: str | None = None,
                     timeout_s: int | None = None,
                     aba_ctx_id: str | None = None) -> dict:
        """Launch an nf-core pipeline. Long-running — uses in_tool_ctx
        so phase lines stream to the chat while it executes and
        honours cancel-on-Stop."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import run_nextflow as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl(
                {"pipeline": pipeline, "revision": revision,
                 "profile": profile, "params": params, "outdir": outdir,
                 "timeout_s": timeout_s},
                ctx,
            )
