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
                   fresh: bool = False,
                   title: str | None = None,
                   aba_ctx_id: str | None = None) -> dict:
        """Run Python in the project's scratch workspace. State persists
        across calls within a thread (interactive kernel); pass
        fresh=true for a one-shot subprocess. Pass background=true (or
        let the router decide based on estimated_runtime_min) for
        deferred long-runs.

        Returns plots/tables/files harvested from the run's working dir."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import run_python as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl({
                "code": code, "timeout_s": timeout_s,
                "background": background,
                "estimated_runtime_min": estimated_runtime_min,
                "fresh": fresh, "title": title,
            }, ctx)

    @mcp.tool()
    def run_r(code: str,
              timeout_s: int | None = None,
              aba_ctx_id: str | None = None) -> dict:
        """Execute R in the thread's persistent R (IRkernel) session.
        Shares the working dir with run_python so the two can hand
        files off (CSV/Parquet/RDS). For Bioconductor / DESeq2 /
        edgeR / limma / Seurat work."""
        from core.runtime.tool_ctx import in_tool_ctx
        from content.bio.tools import run_r as _impl
        with in_tool_ctx(aba_ctx_id) as ctx:
            return _impl({"code": code, "timeout_s": timeout_s}, ctx)
