"""In-process MCP server handle (arch3.md Phase 6 / modularity_audit Tier 2 #7).

Companion to `server_handle.ServerHandle` (stdio-subprocess transport).
This variant hosts a FastMCP server in the SAME process as the gateway
and connects to it via the SDK's in-memory transport
(`mcp.shared.memory.create_connected_server_and_client_session`).

Why in-process rather than a real subprocess?
- Bio tools take a `ctx` dict carrying runtime objects (cancel_token,
  threading.Queue progress channels, Jupyter kernel sessions, an open
  SQLite handle, the current project id contextvar). None of those
  serialize across a process boundary. Hosting bio's own server out-of-
  process would mean re-plumbing every one of them through JSON-RPC.
- The modularity payoff doesn't require a subprocess. What matters is
  the SHAPE: one server, declared schemas, uniform dispatch via the
  gateway, structural readiness if we later want to move a tool out.
- Memory transport keeps protocol fidelity (the server still
  initialize()s, list_tools()s, call_tool()s) so external servers and
  the in-process one are indistinguishable from the gateway's POV.

The handle's surface (state, tools, call_tool, shutdown) duck-types
ServerHandle so `gateway.call()` and `gateway.list_tools()` work
uniformly across both transports.
"""
from __future__ import annotations
import asyncio
import enum
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from .server_handle import HandleState, ToolInfo


class _InProcessConfigShim:
    """Tiny shim so `_handles` items with both transports report a
    `.config.name` (the gateway's status() endpoint dereferences this).
    The in-process handle has no ServerConfig because it isn't loaded
    from yaml — name + default_timeout_s are passed in directly."""
    def __init__(self, name: str, default_timeout_s: int = 30):
        self.name = name
        self.default_timeout_s = default_timeout_s


@dataclass
class InProcessServerHandle:
    """Host an MCP server in-process via memory streams. The server is
    constructed lazily on connect() — we hold onto the factory so a
    crash recovery rebuilds the server fresh (same pattern as
    ServerHandle.connect() respawning the subprocess)."""
    server_factory: 'ServerFactory'
    config: _InProcessConfigShim
    state: HandleState = HandleState.INIT
    tools: list[ToolInfo] = field(default_factory=list)
    last_error: Optional[str] = None
    restart_attempts: int = 0
    _stack: Optional[AsyncExitStack] = None
    _session: Any = None    # ClientSession — kept opaque to avoid SDK import churn
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def connect(self) -> None:
        """Build a fresh FastMCP server via the factory, open a memory-
        transport client session against it, cache the tool catalog."""
        async with self._lock:
            if self.state == HandleState.CONNECTED:
                return
            self.state = HandleState.CONNECTING
            self.last_error = None
            try:
                server: FastMCP = self.server_factory()
                stack = AsyncExitStack()
                # create_connected_server_and_client_session is an async
                # context manager that yields a ready ClientSession;
                # entering it via AsyncExitStack keeps it open until
                # shutdown.
                cm = create_connected_server_and_client_session(server)
                session = await stack.enter_async_context(cm)
                listed = await session.list_tools()
                self.tools = [
                    ToolInfo(
                        name=f"{self.config.name}:{t.name}",
                        raw_name=t.name,
                        description=t.description or "",
                        input_schema=t.inputSchema or {"type": "object"},
                        server=self.config.name,
                    )
                    for t in listed.tools
                ]
                self._stack = stack
                self._session = session
                self.state = HandleState.CONNECTED
                self.restart_attempts = 0
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                self.state = HandleState.DISCONNECTED
                if self._stack is not None:
                    try:
                        await self._stack.aclose()
                    except Exception:  # noqa: BLE001
                        pass
                self._stack = None
                self._session = None

    async def shutdown(self) -> None:
        async with self._lock:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:  # noqa: BLE001
                    pass
            self._stack = None
            self._session = None
            self.state = HandleState.DISCONNECTED

    async def call_tool(self, raw_name: str, arguments: dict,
                        timeout_s: Optional[int] = None) -> dict:
        """Invoke a tool on the in-process server. Mirrors
        ServerHandle.call_tool — same wire shape, same timeout
        contract, same restart-on-crash."""
        if self.state != HandleState.CONNECTED or self._session is None:
            return {"status": "error",
                    "note": f"MCP server {self.config.name!r} not connected "
                            f"(state={self.state.value}); last error: {self.last_error}"}
        deadline = timeout_s if timeout_s is not None else self.config.default_timeout_s
        try:
            result = await asyncio.wait_for(
                self._session.call_tool(raw_name, arguments),
                timeout=deadline,
            )
        except asyncio.TimeoutError:
            return {"status": "error",
                    "note": f"MCP call to {self.config.name}:{raw_name} timed out after {deadline}s"}
        except Exception as e:  # noqa: BLE001
            self.last_error = f"{type(e).__name__}: {e}"
            self.state = HandleState.DISCONNECTED
            asyncio.create_task(self._maybe_restart())
            return {"status": "error",
                    "note": f"MCP call to {self.config.name}:{raw_name} failed: {self.last_error}"}

        # Translate MCP content into the simple wire shape the dispatcher
        # already consumes for stdio servers.
        out_text_parts: list[str] = []
        for c in result.content:
            t = getattr(c, "type", None)
            if t == "text":
                out_text_parts.append(getattr(c, "text", ""))
            else:
                out_text_parts.append(f"[{t or 'binary'} content]")
        return {
            "status": "error" if result.isError else "ok",
            "content": "\n".join(out_text_parts),
            "is_error": bool(result.isError),
        }

    async def _maybe_restart(self) -> None:
        """In-process restarts are cheap (no subprocess respawn) but we
        still cap them — a constantly-crashing factory points at a real
        bug, not a transient transport issue."""
        from .server_handle import _MAX_RESTART_ATTEMPTS, _RESTART_BACKOFF_S
        if self.restart_attempts >= _MAX_RESTART_ATTEMPTS:
            self.state = HandleState.DEAD
            return
        delay = _RESTART_BACKOFF_S[min(self.restart_attempts,
                                       len(_RESTART_BACKOFF_S) - 1)]
        self.restart_attempts += 1
        await asyncio.sleep(delay)
        await self.connect()


class ServerFactory(Protocol):
    """A zero-arg callable that returns a FRESH FastMCP server. Called
    on every connect() so a crashed handle gets a clean rebuild."""
    def __call__(self) -> FastMCP: ...
