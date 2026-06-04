"""aba_core — in-process MCP server hosting bio's tool catalogue.

Phase 6 of the modularity refactor (misc/phase6_mcp_wrapping.md). Today
this server is empty; sub-phases 6.B–6.H migrate `bio/tools.py`'s 46
functions onto it cluster by cluster. 6.I removes the legacy `EXECUTORS`
dispatch entirely.

Usage from gateway startup:

    from core.runtime.mcp import register_inprocess_server
    from content.bio.mcp_servers.aba_core import make_server
    register_inprocess_server('aba_core', make_server)

`make_server` is a factory (zero-arg → fresh FastMCP) — the gateway
calls it on every connect attempt so a crashed handle gets a clean
rebuild.
"""
from .server import make_server   # noqa: F401
