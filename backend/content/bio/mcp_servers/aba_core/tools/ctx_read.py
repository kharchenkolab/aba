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

from typing import Literal

from mcp.server.fastmcp import FastMCP


def register_ctx_read_tools(mcp: FastMCP) -> None:
    """Register the ctx-aware read tools on `mcp`."""

    @mcp.tool()
    def Skill(skill: str | None = None, args: str = "",
              aba_ctx_id: str | None = None) -> dict:
        """Fetch (load) a registered skill / recipe BY NAME and return
        its body. THIS DOES NOT EXECUTE — your NEXT call must be
        `run_python` (or `run_r`) with the code block from the body.

        Skill names are NOT separate tools. The only way to invoke a
        skill is `Skill(skill="<name>", args=...)`. Recipe NAMES from
        search_skills cannot be called directly — those calls fail
        with "Unknown tool". Always wrap them in Skill(skill="…").

        Typical flow:
          1. Skill(skill="fetch-geo-processed-matrices", args="GSE192391")
             → returns {body: "<recipe markdown with python code>"}
          2. run_python(code="<the python from the body>")
             → actually performs the work.

        Skill itself is a doc-loader. If you stop after Skill without
        running its code, nothing has been done."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import skill_tool
        return skill_tool({"skill": skill, "args": args},
                          peek_ctx(aba_ctx_id))

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
    def get_lineage(entity_id: str,
                    direction: Literal["up", "down", "both"] = "up",
                    max_depth: int = 8) -> dict:
        """Lineage of an entity over the derivation graph. Returns a graph +
        a human-readable summary.

        direction:
          • 'up'   (default) — PROVENANCE: what produced this (figure → result →
                    analysis → dataset). For a long revision chain, raise max_depth
                    or use `list_revisions` (depth-unbounded, v1…vN labelled).
          • 'down' — DEPENDENTS: what depends on this. Check before archive/supersede
                    to spot what would orphan.
          • 'both' — {'upstream': …, 'downstream': …}.

        `max_depth` (default 8) bounds the walk in each direction."""
        from content.bio.tools import get_provenance as _up
        from content.bio.tools import get_dependents as _down
        arg = {"entity_id": entity_id, "max_depth": max_depth}
        if direction == "down":
            return _down(arg)
        if direction == "both":
            return {"upstream": _up(arg), "downstream": _down(arg)}
        return _up(arg)

    @mcp.tool()
    def read_capability(name: str) -> dict:
        """Full detail for one capability by name — what it does, its
        inputs, and (for a reference entry) where the implementation
        lives. Mirrors read_skill: list/search stay trimmed; this
        expands one on demand."""
        from content.bio.tools import read_capability as _impl
        return _impl({"name": name})

    @mcp.tool()
    def read_csv_info(path: str) -> dict:
        """CSV/TSV preview — first 5 rows + column types. Use after
        register_dataset to confirm the file shape before
        run_python/run_r. `path` is a filename or path under the project."""
        from content.bio.tools import read_csv_info as _impl
        return _impl({"filename": path})
