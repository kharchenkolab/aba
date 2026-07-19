"""Phase 6.H — run_python + run_r (the heavy two).

The most-used tools and the most ctx-coupled:

  - cancel_token (observer-pattern; bio impl registers killpg on it,
    gateway also registers fut.cancel for asyncio task cancellation)
  - progress_q (threading.Queue — handler thread must rebind via
    in_tool_ctx so kernel/install phase lines stream)
  - thread_id (kernel session lookup)
  - focus_entity_id (background-job submission)
  - run_id (stateless fallback path)

All of these are preserved by the stash-by-id ctx store (they're real
Python objects, not just data). The handler peeks ctx, binds the
progress sink, and delegates to the existing bio impl unchanged.

Cancellation semantics: a Stop click fires cancel_token. Two callbacks
are registered:
  1. gateway: fut.cancel() — cancels the asyncio task awaiting the
     handler. Returns {status:cancelled} to the dispatcher.
  2. bio impl: killpg(subprocess) — kills the subprocess actually
     running the user's Python/R. The handler thread sees this as
     a cancelled status from sess.execute().
Both fire on the SAME cancel_token object (reachable via peek_ctx),
so both reach their intended teardown.
"""
from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

# Per-parameter descriptions for the placement estimate. These reach the model as
# JSON-schema `description`s (FastMCP/Pydantic), i.e. AT THE DECISION POINT, in
# addition to the tool docstring. The tool-presentation policy decides delivery per
# tier: `standard`/`full` KEEP them (the production agent gets the guidance); the
# budget-bound `lean` tiers DROP them (recoverable via describe_tool). Shared by
# run_python + run_r. See misc/tool_presentation.md.
_D_BACKGROUND = (
    "Run ASYNC in a fresh process instead of the interactive kernel. On a cluster this "
    "is an sbatch job; use it when the step needs more cores/mem/GPU than this node has, "
    "may exceed the session's remaining walltime, or is much faster on Slurm. A background "
    "run cannot see interactive objects — it loads inputs from disk and writes outputs to disk.")
_D_EST_RT = (
    "Rough wall-clock estimate in MINUTES. Sizes the background job's timeout (~2x this) and "
    "informs the partition/QoS choice. NOT an auto-background trigger.")
_D_EST_CORES = (
    "Peak CPU cores the step needs. Drives the Slurm partition/QoS and the local-vs-Slurm "
    "routing decision. Leave unset only for a genuinely small step (treated as ~1 core).")
_D_EST_MEM = (
    "Peak RAM in GB the step needs. Drives partition/QoS and routing — a value larger than "
    "this node's memory routes the job to Slurm.")
_D_EST_GPU = (
    "Set True for any GPU workload: deep learning (torch/JAX), scVI/scANVI/scArches, RAPIDS/"
    "CUDA, etc. On a cluster this selects a GPU node/partition. Left False (the default) the "
    "job is placed on CPU — so a GPU step that omits this runs slowly on CPU or fails.")
_D_EXECUTION = (
    "With background=True only: 'slurm' (default on a cluster) queues an sbatch job; 'local' "
    "runs it in ABA's own allocation with no queue wait, good when it fits here; 'auto' decides "
    "from the estimate.")
_D_SITE = (
    "Run this step ON a declared remote machine (names from describe_compute); overrides "
    "`execution`. WITHOUT background: synchronous, in a PERSISTENT session on that machine — "
    "variables and loaded objects survive between your site= calls (multi-step work needs no "
    "reload-from-disk each step; pass fresh=true for a clean one-shot process). Read paths "
    "valid on that machine; small outputs come back automatically, large ones stay there "
    "kept-addressable. WITH background=True: a deferred job for LONG steps — you're resumed "
    "when it finishes; do NOT poll. Prefer the machine that already holds the inputs over "
    "transferring data. Isolated envs travel: realized on the site, re-locked for its "
    "platform automatically.")


def register_run_exec_tools(mcp: FastMCP) -> None:
    """Register run_python + run_r on `mcp`. Uses in_tool_ctx so the
    progress sink is bound on the handler thread — without that,
    multi-minute kernel installs and pipeline runs would go silent."""

    @mcp.tool()
    def run_python(code: str,
                   timeout_s: int | None = None,
                   background: Annotated[bool, Field(description=_D_BACKGROUND)] = False,
                   estimated_runtime_min: Annotated[float | None, Field(description=_D_EST_RT)] = None,
                   est_cores: Annotated[int | None, Field(description=_D_EST_CORES)] = None,
                   est_mem_gb: Annotated[int | None, Field(description=_D_EST_MEM)] = None,
                   est_gpu: Annotated[bool, Field(description=_D_EST_GPU)] = False,
                   fresh: bool = False,
                   title: str | None = None,
                   env: str | None = None,
                   execution: Annotated[str | None, Field(description=_D_EXECUTION)] = None,
                   site: Annotated[str | None, Field(description=_D_SITE)] = None,
                   aba_ctx_id: str | None = None) -> dict:
        """Run Python in the project's scratch workspace. State persists
        across calls within a thread (interactive kernel); pass
        fresh=true for a one-shot subprocess.

        WHERE IT RUNS (see the per-turn "Compute environment:" line /
        `describe_compute`, and the `compute-placement` knowhow):
        - Default: the interactive kernel — state persists. A LONG cell is
          fine here; just raise `timeout_s`. Do NOT background to avoid a
          timeout.
        - `background=True`: runs ASYNC in a FRESH process — it has NONE of
          your interactive objects, so it must load its inputs from disk and
          write outputs to disk (the `object 'panel' not found` trap). On a
          LOCAL deployment use this only when the user asks or to fan out
          several independent jobs. On a SLURM deployment a background run is
          an `sbatch` job — use it when the step needs more cores/mem/GPU than
          this node has, might exceed the session's remaining walltime, or
          would be meaningfully faster on Slurm; size it with est_cores /
          est_mem_gb / est_gpu / estimated_runtime_min (mapped to a
          partition/QoS; ignored off-cluster). `estimated_runtime_min` is a
          sizing/ walltime hint, NOT an auto-background trigger — and it sets the
          background job's TIMEOUT ceiling (~2x the estimate). Give a realistic
          `estimated_runtime_min` (or an explicit `timeout_s`) for a long job;
          background jobs are NOT capped at the interactive 30-min limit.
          `execution` (with background=True): `'slurm'` (default on a cluster) submits an
          sbatch job; `'local'` runs it in-place in ABA's OWN allocation — no queue wait —
          when it fits (good for a quick background job); `'auto'` decides from the estimate.
          `site` runs the step ON a declared remote machine (data gravity:
          prefer the machine holding the inputs); it overrides `execution`.
          A short step: `site` alone — synchronous, in a PERSISTENT session
          there (state survives between site= calls; fresh=true for a clean
          one-shot). A long step: `site` + `background=True` —
          deferred; you're resumed when it finishes (don't poll). Never claim
          work ran on a machine unless the job actually executed there — the
          result names where it ran.

        ENVIRONMENT: omit `env` (or `env='default'`) for the project's
        normal environment. Pass `env='name'` to run inside an isolated
        environment you created with `make_isolated_env(name='name')` —
        used when a package conflicts with the base. `env` combines with
        `background=True`: a long job runs IN that env (its own python),
        as a Slurm job on a compute node when on a cluster. On a REMOTE
        step (`site=`), `env='system'` runs on the node's own interpreter
        with NO environment realization — right for ANY quick step whose
        code imports nothing beyond the stdlib: downloads/transfers, file
        listings and checksums, quick counts/sums, existence checks. On a
        machine where the project environment isn't realized yet, a
        default-env step first builds it (GBs, minutes) — don't trigger
        that for a stdlib one-liner; reach for the full environment only
        when the code actually imports scientific libraries.

        INSTALLING PACKAGES: to use a library that isn't already in the
        sandbox, call `ensure_capability(name)` FIRST — NEVER `pip install`,
        `!pip`, or a `subprocess` package install. `ensure_capability` uses
        prebuilt wheels/conda binaries. (The stdlib — urllib, json, os, … —
        is always present; import it directly.)

        Returns plots/tables/files harvested from the run's working dir.

        ROUTING NOTE: When the goal is a MODIFIED VERSION of an existing
        focused figure/table (different format like PDF/SVG, different
        layout/style, or a content tweak), prefer `make_revision`
        instead — it pins the new rendering into the figure's revision
        chain so the user sees it as a sibling on the focused Result.
        Use `run_python` for analysis NOT tied to an existing focused
        entity (loading data, fitting models, computing new tables,
        exploratory plots that aren't a derivative of a current figure).
        Producing a one-off PDF via this tool is correct only when no
        focused figure is the parent of the request."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import run_python as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl({
                "code": code, "timeout_s": timeout_s,
                "background": background,
                "estimated_runtime_min": estimated_runtime_min,
                "est_cores": est_cores, "est_mem_gb": est_mem_gb, "est_gpu": est_gpu,
                "fresh": fresh, "title": title, "env": env, "execution": execution,
                "site": site,
            }, ctx)

    @mcp.tool()
    def run_r(code: str,
              timeout_s: int | None = None,
              background: Annotated[bool, Field(description=_D_BACKGROUND)] = False,
              estimated_runtime_min: Annotated[float | None, Field(description=_D_EST_RT)] = None,
              est_cores: Annotated[int | None, Field(description=_D_EST_CORES)] = None,
              est_mem_gb: Annotated[int | None, Field(description=_D_EST_MEM)] = None,
              est_gpu: Annotated[bool, Field(description=_D_EST_GPU)] = False,
              title: str | None = None,
              env: str | None = None,
              execution: Annotated[str | None, Field(description=_D_EXECUTION)] = None,
              site: Annotated[str | None, Field(description=_D_SITE)] = None,
              aba_ctx_id: str | None = None) -> dict:
        """Execute R in the thread's persistent R (IRkernel) session.
        Shares the working dir with run_python so the two can hand
        files off (CSV/Parquet/RDS). For Bioconductor / DESeq2 /
        edgeR / limma / Seurat work.

        ENVIRONMENT: omit `env` for the project's normal R library
        (which already overrides the base). Pass `env='name'` only for a
        fully isolated R library you made with
        `make_isolated_env(name='name', language='r')`.

        INSTALLING PACKAGES: to use an R package that isn't loaded, call
        `ensure_capability(name)` FIRST — NEVER `install.packages()`,
        `BiocManager::install()`, or `devtools::install_github()` in R
        code. Those source-compile against system libs that aren't here
        and fail; `ensure_capability` installs the prebuilt conda/bioconda
        binary. (For a public database — GEO, SRA, ENA — `ensure_capability`
        the maintained package, e.g. GEOquery, rather than hand-rolling.)

        WHERE IT RUNS (same rules as run_python; see the per-turn "Compute
        environment:" line / `describe_compute` / the `compute-placement`
        knowhow): default is the interactive R kernel (state persists; a LONG
        cell is fine — raise `timeout_s`, don't background to dodge a timeout).
        `background=True` runs as a queued Rscript job in a FRESH process — it
        loads its inputs from disk and writes outputs to disk (no interactive
        objects), with artifacts harvested + the plan continuation re-firing on
        completion. On LOCAL use it only when the user asks or to fan out
        independent jobs; on SLURM use it when the step needs more cores/mem/GPU
        than this node has or might exceed the remaining walltime, sized with
        est_cores/est_mem_gb/est_gpu/estimated_runtime_min. Do NOT shell out to
        Rscript via `run_python(subprocess.run(['Rscript', ...]))` —
        background=True IS the supported path for long R work. `site` runs the
        step ON a declared remote machine — same rules as run_python's `site`
        (alone = synchronous fresh process there; + background for long steps).

        ROUTING NOTE: When the goal is a MODIFIED VERSION of an existing
        focused figure/table (cairo_pdf of a current figure, ggsave with
        new theme/layout, ComplexHeatmap re-render with different legend
        placement, etc.), prefer `make_revision` — it pins the new
        rendering into the figure's revision chain so the user sees it
        as a sibling on the focused Result. Use `run_r` for analysis
        NOT tied to an existing focused entity (loading/processing data,
        fitting models, computing new tables, exploratory plots that
        aren't a derivative of a current figure). Writing a one-off
        PDF/SVG via this tool is correct only when no focused figure is
        the parent of the request."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import run_r as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl({"code": code, "timeout_s": timeout_s,
                          "background": background, "execution": execution,
                          "site": site,
                          "estimated_runtime_min": estimated_runtime_min,
                          "est_cores": est_cores, "est_mem_gb": est_mem_gb,
                          "est_gpu": est_gpu,
                          "title": title, "env": env}, ctx)
