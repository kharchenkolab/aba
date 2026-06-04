"""Simple bio tools — no ctx, pure (args) → result functions.

6.A: stub (no tools registered yet).
6.B: migrate `list_capabilities`, `read_memory`, `search_pypi` here.

The pattern, anticipated for 6.B:

    @mcp.tool()
    def list_capabilities(category: str = "") -> dict:
        from content.bio.tools import list_capabilities_tool
        return list_capabilities_tool({"category": category})

The FastMCP `@tool` decorator generates the JSON schema from the
function signature + docstring, so a single Python annotation replaces
the duplicate TOOL_SCHEMAS dictionary entry today.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_simple_tools(mcp: FastMCP) -> None:
    """No-op until 6.B. Called by aba_core/server.py once it starts
    populating clusters. Kept as a module so the wiring point is
    obvious and future sub-phases just edit this file (or its peers)."""
    return None
