"""Phase 6.G — plan / write-memory / runtime-control cluster.

  - present_plan, ask_clarification: agent-side gestures that guide.py
    intercepts BEFORE the dispatcher runs (the bio impl is a stub
    returning a placeholder status). We register them on aba_core so
    the schema lives here too — post 6.I they're the only way the
    agent learns these tools exist.
  - write_memory: persist a note across sessions
  - restart_kernel: clear the python/r kernel state in this thread
  - run_nextflow: launch an nf-core pipeline (long-running)

Scenarios (`create_scenario`) were removed from the catalog 2026-06-06:
the variant-figure flow is now covered by `make_revision` in the
revisions cluster (Stage 5 of misc/exec_records_and_versioning.md).
The underlying lifecycle helper `create_scenario_variant` is still in
the codebase as a private code path; bringing scenarios back as a
distinct user concept is on the roadmap for after v1 (branches +
"what-if" exploration UX).
"""
from __future__ import annotations

from typing import Literal, TypedDict

from mcp.server.fastmcp import FastMCP


class PlanStep(TypedDict, total=False):
    """One step of a plan. `title` is the user-facing one-line label and is
    required — do NOT name this field `step` or `name`. Optional: `description`
    (one sentence of detail), `expected_outputs` (filenames the step will
    produce), `skill` (name of a recipe the step follows), `parameters`
    (resolved choices as a dict)."""
    # Implementation note (NOT part of the schema description shown to the
    # model — placed BELOW the docstring so FastMCP doesn't pick it up):
    # this typed shape exists so FastMCP emits per-item properties in the
    # generated JSON schema. Without it, the schema is `array of anything`
    # and earlier Opus iterations were caught using `step` instead of `title`
    # (pre-WU-1 the contract was carried by manual TOOL_SCHEMAS at commit
    # 862d55b; restored at the MCP layer post-WU-1). Leaving meta-language
    # like "infer field names from prose" or model-incident lore in the
    # docstring itself destabilized the model at the assumptions→steps
    # boundary (prj_2578185f thr_577d666a, 2026-06-09) — both
    # present_plan calls slipped from JSON to XML mid-emission. Keep this
    # block as a normal Python comment so the schema stays clean.
    #
    # `total=False`: every field schema-described-but-optional so a plain
    # string is still tolerated by the tool dispatcher (the bio impl coerces).
    title: str
    description: str
    expected_outputs: list[str]
    skill: str
    parameters: dict


def register_plan_etc_tools(mcp: FastMCP) -> None:
    """Register the plan / write-memory / runtime tools on `mcp`.

    Note: create_scenario was removed 2026-06-06 — variant-figure
    creation goes through `make_revision` in the revisions cluster
    (Stage 5 of misc/exec_records_and_versioning.md). Scenarios are
    a post-v1 concept; their lifecycle helper is preserved but not
    exposed to the agent.
    """

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
                     background: bool = True,
                     execution: str | None = None,
                     estimated_runtime_min: float | None = None,
                     aba_ctx_id: str | None = None) -> dict:
        """Launch a Nextflow / nf-core pipeline (e.g. 'nf-core/rnaseq').

        background=True (the DEFAULT, recommended for real pipelines): the head runs
        as a long Slurm job that fans tasks out via the site executor; returns a
        deferred handle and resumes you on completion. background=False runs it
        synchronously here — only for tiny `-profile test` smoke runs. `profile`
        (e.g. 'test,cbe') and `params` (the pipeline's `--<k> <v>`) are passed through.

        execution: "slurm" (default) submits each task as its own Slurm job — use for
        heavy real-data runs. "local" runs all tasks on the head's own (larger) node
        allocation — far faster for small or `-profile test` pipelines, where per-task
        Slurm queue latency would otherwise dwarf the seconds of actual compute."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import run_nextflow as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl(
                {"pipeline": pipeline, "revision": revision,
                 "profile": profile, "params": params, "outdir": outdir,
                 "timeout_s": timeout_s, "background": background, "execution": execution,
                 "estimated_runtime_min": estimated_runtime_min},
                ctx,
            )

    @mcp.tool()
    def describe_pipeline(pipeline: str, revision: str | None = None,
                          aba_ctx_id: str | None = None) -> dict:
        """Inspect a Nextflow / nf-core pipeline before running it. Returns its run
        parameters (required, types, defaults, allowed values, help — grouped), the
        latest release, AND `input_format`: the exact samplesheet columns the pipeline
        expects (name, required, type, allowed values) so you can BUILD the `--input`
        file from the user's data, plus `docs` (fetchable links to usage.md / output.md /
        README / the nf-co.re page) to read if anything is unclear. Call this BEFORE
        run_nextflow. Returns a note if the pipeline ships no schema."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import describe_pipeline as _impl
        return _impl({"pipeline": pipeline, "revision": revision}, peek_ctx(aba_ctx_id))
