"""MCP gateway — exposes a uniform tool dispatch surface for tools that
live in separate processes (MCP servers) alongside the in-process
bio/tools.py executors.

Goal: importing a large external library of tools (lakefs, hpc,
biomni-*) shouldn't require hand-wrapping each one in bio/tools.py.
The gateway:
  - spawns configured MCP servers at app startup
  - fetches their tool catalogs
  - exposes name-prefixed tools to the Guide loop ('lakefs:read_object',
    'biomni:annotate', …)
  - routes calls from sync code into its background async loop

Configuration: bio/mcp/servers.yaml (empty by default). When any server
is configured, startup spawns them; if none, the gateway is a no-op.

Public API:
  start_all() / shutdown()       — lifecycle
  list_tools()                   — schemas in wire shape for the Guide
  call(name, input)              — sync dispatch (blocks until result)
  is_mcp_tool(name)              — name belongs to a registered server
  status()                       — admin: per-server health + tool counts
"""
from .gateway import (
    start_all,
    add_server,
    register_inprocess_server,
    shutdown,
    list_tools,
    call,
    is_mcp_tool,
    status,
    _reset_for_testing,
)
from .config import ServerConfig

__all__ = ["start_all", "add_server", "register_inprocess_server",
           "shutdown", "list_tools", "call", "is_mcp_tool",
           "status", "ServerConfig", "_reset_for_testing"]
