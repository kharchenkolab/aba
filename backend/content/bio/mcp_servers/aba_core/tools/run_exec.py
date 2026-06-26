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

from mcp.server.fastmcp import FastMCP


def register_run_exec_tools(mcp: FastMCP) -> None:
    """Register run_python + run_r on `mcp`. Uses in_tool_ctx so the
    progress sink is bound on the handler thread — without that,
    multi-minute kernel installs and pipeline runs would go silent."""

    @mcp.tool()
    def run_python(code: str,
                   timeout_s: int | None = None,
                   background: bool = False,
                   estimated_runtime_min: float | None = None,
                   est_cores: int | None = None,
                   est_mem_gb: int | None = None,
                   est_gpu: bool = False,
                   fresh: bool = False,
                   title: str | None = None,
                   env: str | None = None,
                   aba_ctx_id: str | None = None) -> dict:
        """Run Python in the project's scratch workspace. State persists
        across calls within a thread (interactive kernel); pass
        fresh=true for a one-shot subprocess. Pass background=true (or
        let the router decide based on estimated_runtime_min) for
        deferred long-runs. On an HPC deployment a backgrounded run
        becomes a Slurm job — optionally size it with est_cores /
        est_mem_gb / est_gpu (mapped to a partition/QoS by the
        deployment); these are ignored when not on a cluster.

        ENVIRONMENT: omit `env` (or `env='default'`) for the project's
        normal environment. Pass `env='name'` to run inside an isolated
        environment you created with `make_isolated_env(name='name')` —
        used when a package conflicts with the base. `env` combines with
        `background=True`: a long job runs IN that env (its own python),
        as a Slurm job on a compute node when on a cluster.

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
                "fresh": fresh, "title": title, "env": env,
            }, ctx)

    @mcp.tool()
    def run_r(code: str,
              timeout_s: int | None = None,
              background: bool = False,
              estimated_runtime_min: float | None = None,
              est_cores: int | None = None,
              est_mem_gb: int | None = None,
              est_gpu: bool = False,
              title: str | None = None,
              env: str | None = None,
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

        Pass `background=True` (or `estimated_runtime_min` above the
        router threshold, ~5 min) for long Seurat integrations / DESeq2
        sweeps / etc. — those run as queued Rscript jobs that don't
        block the thread, with artifacts harvested + the plan
        continuation re-firing on completion. Do NOT shell out to
        Rscript via `run_python(subprocess.run(['Rscript', ...]))` —
        background=True IS the supported path for long R work.

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
                          "background": background,
                          "estimated_runtime_min": estimated_runtime_min,
                          "est_cores": est_cores, "est_mem_gb": est_mem_gb,
                          "est_gpu": est_gpu,
                          "title": title, "env": env}, ctx)
