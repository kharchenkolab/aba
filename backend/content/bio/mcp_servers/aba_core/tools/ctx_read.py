"""Phase 6.C — ctx-aware read tools.

Seven tools that either USE the per-call ctx (Skill / read_skill) or are
adjacent to the ctx infrastructure rollout (pure read-only tools that
get migrated alongside to round out the cluster).

The ctx-USE pattern:

    @mcp.tool()
    def Skill(skill: str, args: str = "",
              aba_ctx_id: str | None = None) -> dict:
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import skill_tool
        return skill_tool({"skill": skill, "args": args},
                          peek_ctx(aba_ctx_id))

`aba_ctx_id` is the hidden field the dispatcher injects (see
backend/core/runtime/tool_ctx.py). It never appears in TOOL_SCHEMAS so
the agent doesn't see it. `peek_ctx` returns {} when the id is absent,
which the bio impls treat as 'no ctx, use defaults'.

The pure-read tools (get_provenance, get_dependents, read_capability,
read_csv_info) don't declare `aba_ctx_id` — they don't read ctx, so
the dispatcher's injection is harmless overhead but the schema stays
minimal.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_ctx_read_tools(mcp: FastMCP) -> None:
    """Register the ctx-aware read tools on `mcp`."""

    @mcp.tool()
    def Skill(skill: str | None = None, args: str = "",
              aba_ctx_id: str | None = None) -> dict:
        """Execute a skill within the main conversation. `$ARGUMENTS` in
        the skill body is substituted with `args`."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import skill_tool
        return skill_tool({"skill": skill, "args": args},
                          peek_ctx(aba_ctx_id))

    @mcp.tool()
    def read_skill(name: str | None = None,
                   aba_ctx_id: str | None = None) -> dict:
        """Deprecated — use `Skill` instead. Loads the full body of a
        registered skill by name. Kept one release for back-compat
        with models that still emit the old name."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import read_skill as _impl
        return _impl({"name": name}, peek_ctx(aba_ctx_id))

    @mcp.tool()
    def list_entities(type: str | None = None, query: str | None = None,
                      limit: int = 30,
                      aba_ctx_id: str | None = None) -> dict:
        """List/find entities in this project (datasets, figures, tables,
        results, findings, claims, narratives). Discovery hook before
        pinning, promoting, annotating, or citing as evidence."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import list_entities_tool
        return list_entities_tool(
            {"type": type, "query": query, "limit": limit},
            peek_ctx(aba_ctx_id),
        )

    # --- pure read tools (no ctx use) ---

    @mcp.tool()
    def get_provenance(entity_id: str, max_depth: int = 8) -> dict:
        """Provenance trace for an entity — what produced it
        (upstream). Returns a graph + a human-readable summary.

        `max_depth` (default 8) controls how far back the walk goes.
        For a long revision chain (figure with many revisions), pass
        a larger number — or use `list_revisions` directly, which is
        depth-unbounded and pre-labels every entry with its v1…vN
        version number. Use get_provenance for cross-type derivation
        traces (figure → result → analysis → dataset)."""
        from content.bio.tools import get_provenance as _impl
        return _impl({"entity_id": entity_id, "max_depth": max_depth})

    @mcp.tool()
    def get_dependents(entity_id: str, max_depth: int = 8) -> dict:
        """Downstream entities — what depends on this one. Useful
        before archive/supersede to spot what would orphan.

        `max_depth` (default 8) controls how far down the walk goes."""
        from content.bio.tools import get_dependents as _impl
        return _impl({"entity_id": entity_id, "max_depth": max_depth})

    @mcp.tool()
    def read_capability(name: str | None = None,
                        capability: str | None = None) -> dict:
        """Full detail for one capability by name — what it does, its
        inputs, and (for a reference entry) where the implementation
        lives. Mirrors read_skill: list/search stay trimmed; this
        expands one on demand."""
        from content.bio.tools import read_capability as _impl
        return _impl({"name": name, "capability": capability})

    @mcp.tool()
    def read_csv_info(filename: str) -> dict:
        """CSV/TSV preview — first 5 rows + column types. Use after
        register_dataset to confirm the file shape before
        run_python/run_r."""
        from content.bio.tools import read_csv_info as _impl
        return _impl({"filename": filename})
