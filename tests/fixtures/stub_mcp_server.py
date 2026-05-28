"""Minimal stdio MCP server for the item-3 live-adoption test.

Spawned by ensure_capability(mcp_server) via the gateway; exposes one tool
('echo') so the test can assert the adopted server's tool becomes callable.
"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("stub")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the input text."""
    return f"echo: {text}"


if __name__ == "__main__":
    mcp.run()  # stdio transport by default
