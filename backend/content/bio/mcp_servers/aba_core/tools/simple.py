"""Simple bio tools — no `ctx`, pure (args) → result functions.

Phase 6.B (misc/phase6_mcp_wrapping.md): the FIRST migration cluster.
Three tools whose signatures take only `input_: dict` today:
`search_capabilities`, `read_memory` (search moved to search_registry). None of them touch
runtime objects (cancel_token, kernel session, progress queue, etc.) —
ideal as the pattern-establisher.

Each handler delegates to the existing bio/tools.py impl. The legacy
EXECUTORS entries stay in place for the duration of the migration
(belt-and-suspenders); the bio dispatcher prefers the aba_core route
via `is_inprocess_tool`. Phase 6.I removes both EXECUTORS and
TOOL_SCHEMAS entries for everything that's been migrated.

The @mcp.tool() decorator generates the JSON schema from the function
signature + docstring, so this file is the single source of truth for
both impl and schema once 6.I lands.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_simple_tools(mcp: FastMCP) -> None:
    """Register the no-ctx, pure-input tools onto `mcp`. Imports the
    bio impls lazily inside each handler to keep the server-construction
    cheap (the factory runs on every reconnect)."""

    @mcp.tool()
    def search_capabilities(query: str | None = None,
                            tags: list[str] | None = None) -> dict:
        """Search the CURATED capability catalog (tools/packages ABA already knows).
        Intent-ranked (BM25 + substring) when a query is given, plain tag-filter
        otherwise. Returns a trimmed view for the model. (Companion searches:
        search_skills for recipes, search_registry for external registries.)"""
        from content.bio.tools import list_capabilities_tool
        return list_capabilities_tool({"query": query, "tags": tags})

    @mcp.tool()
    def read_memory(name: str) -> dict:
        """Read one of your own saved notes from a past session by name.
        Returns body + caveat — these are reference notes, NOT facts to
        cite; verify against the live source before relying on specifics."""
        from content.bio.tools import read_memory_tool
        return read_memory_tool({"name": name})
