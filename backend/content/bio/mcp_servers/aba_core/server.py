"""FastMCP server factory for aba_core.

A zero-arg factory function that returns a fresh `FastMCP` instance.
The gateway calls this on every (re)connect so a crashed handle gets a
clean rebuild. Each sub-phase grows the registration block at the
bottom; 6.A is the empty scaffold.

Why a factory rather than a module-level singleton?
- `mcp.server.fastmcp.FastMCP` holds anyio state internally; reusing
  one across reconnects after an exception is fragile.
- A factory matches the stdio-subprocess pattern (every reconnect
  respawns the process); same restart-on-crash semantics carry over.
- Tests can construct a server in isolation without globals.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .tools.simple import register_simple_tools


def make_server() -> FastMCP:
    """Build a fresh aba_core MCP server.

    Tool registration happens here (or via per-cluster
    `register_<cluster>` helpers). 6.A intentionally registers zero
    tools — the goal is to prove the wiring: server starts, gateway
    connects via memory transport, `list_tools()` reports 0 tools."""
    mcp = FastMCP(
        name="aba_core",
        instructions=(
            "In-process MCP server hosting ABA bio's tool catalogue. "
            "Phase 6 of arch3.md — see misc/phase6_mcp_wrapping.md. "
            "All tools are exposed via the same gateway channel as "
            "external stdio servers (lakefs, biomni, ...). Tools "
            "requiring runtime context (cancel_token, kernel session) "
            "read it from a contextvar set by the dispatcher before "
            "the call lands here."
        ),
    )

    # Per-cluster registrations — each is a no-op today, populated by
    # subsequent sub-phases. Keeping them as explicit calls makes the
    # migration progress legible at a glance.
    register_simple_tools(mcp)

    return mcp
