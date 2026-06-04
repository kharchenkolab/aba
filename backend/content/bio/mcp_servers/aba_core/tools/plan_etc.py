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

from typing import Literal, TypedDict

from mcp.server.fastmcp import FastMCP


class PlanStep(TypedDict, total=False):
    """One step of a present_plan list.

    NOTE: this typed shape exists so FastMCP emits per-item properties in the
    generated JSON schema (`steps.items.properties = {title, ...}`). Without it,
    the schema is just `array of anything` and the model has to *infer* field
    names from prose — Opus 4.7 was caught using `step` instead of `title`
    (commit history: this contract was carried by the manual TOOL_SCHEMAS
    pre-WU-1 commit 862d55b; restored here at the MCP layer post-WU-1)."""
    # title is the user-facing label — required in the *intended* contract.
    # `total=False` keeps the field schema-described-but-optional so a plain
    # string is still tolerated by the tool dispatcher (the bio impl coerces).
    title: str
    description: str
    expected_outputs: list[str]
    skill: str
    parameters: dict


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
    def present_plan(steps: list[PlanStep],
                     title: str | None = None,
                     summary: str | None = None,
                     assumptions: list[str] | None = None,
                     rationale: str | None = None) -> dict:
        """Present a stepwise plan to the user for approval. The framework
        intercepts this and pauses the turn — the user approves with Go (or
        the auto-fire timer) or asks for adjustments.

        Each step is an object with `title` (the user-facing one-line label;
        REQUIRED — do not name this field `step` or `name`), optional
        `description` (one sentence of detail), optional `expected_outputs`
        (filenames/figures this step will produce), optional `skill` (name of
        a recipe the step follows — e.g. `'scrna-qc-clustering'`), and
        optional `parameters` (a dict of resolved choices). A plain string is
        accepted and coerced to {title}."""
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
    def write_memory(name: str,
                     type: Literal["user", "feedback", "project", "reference"],
                     body: str,
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
