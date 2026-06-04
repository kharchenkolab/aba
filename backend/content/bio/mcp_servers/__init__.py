"""bio.mcp_servers — in-process MCP servers hosting bio's own tools.

Phase 6 (misc/phase6_mcp_wrapping.md) wraps `bio/tools.py`'s 46 functions
as MCP methods on `aba_core` so every bio tool call routes through the
same gateway channel as external stdio servers. No subprocess —
memory transport keeps `ctx` (cancel_token, kernel sessions, etc.)
working unchanged.
"""
