"""Per-MCP-server handle: spawns the process, holds the ClientSession,
caches the tool catalog. Runs entirely inside the gateway's background
event loop — nothing in here is called from sync code directly.

Restart-on-crash with exponential backoff lives at this layer: the
gateway asks `connect()` once and then again on every recovery attempt;
state transitions (CONNECTED / DISCONNECTED / DEAD) drive whether the
server's tools appear in list_tools().
"""
from __future__ import annotations
import asyncio
import enum
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ServerConfig


class HandleState(str, enum.Enum):
    INIT          = 'init'
    CONNECTING    = 'connecting'
    CONNECTED     = 'connected'
    DISCONNECTED  = 'disconnected'   # crashed but eligible for restart
    DEAD          = 'dead'           # too many failures; give up


_MAX_RESTART_ATTEMPTS = 3
_RESTART_BACKOFF_S = (1, 3, 10)   # seconds before each successive retry


@dataclass
class ToolInfo:
    name:        str                          # PREFIXED: "<server>:<original>"
    description: str
    input_schema: dict[str, Any]
    server:      str                          # config.name
    raw_name:    str                          # original name on the server


@dataclass
class ServerHandle:
    config: ServerConfig
    state:  HandleState = HandleState.INIT
    tools:  list[ToolInfo] = field(default_factory=list)
    last_error: Optional[str] = None
    restart_attempts: int = 0
    _stack: Optional[AsyncExitStack] = None
    _session: Optional[ClientSession] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def connect(self) -> None:
        """Spawn the server, open a stdio session, list its tools. Idempotent
        in the sense that a CONNECTED handle short-circuits."""
        async with self._lock:
            if self.state == HandleState.CONNECTED:
                return
            self.state = HandleState.CONNECTING
            self.last_error = None
            try:
                stack = AsyncExitStack()
                params = StdioServerParameters(
                    command=self.config.command,
                    args=list(self.config.args),
                    env=self.config.env or None,
                    cwd=self.config.cwd,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
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
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                self.state = HandleState.DISCONNECTED
                if self._stack is not None:
                    try:
                        await self._stack.aclose()
                    except Exception:
                        pass
                self._stack = None
                self._session = None

    async def shutdown(self) -> None:
        async with self._lock:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:
                    pass
            self._stack = None
            self._session = None
            self.state = HandleState.DISCONNECTED

    async def call_tool(self, raw_name: str, arguments: dict, timeout_s: Optional[int] = None) -> dict:
        """Invoke a tool on this server. Returns a structured wire dict
        suitable for the Guide loop's tool_result content. If the call
        raises (server crash, etc.), transitions to DISCONNECTED and
        schedules a restart attempt; the caller gets a structured error."""
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
        except Exception as e:
            # Schedule a reconnect; let the next call retry through a healthier handle.
            self.last_error = f"{type(e).__name__}: {e}"
            self.state = HandleState.DISCONNECTED
            asyncio.create_task(self._maybe_restart())
            return {"status": "error",
                    "note": f"MCP call to {self.config.name}:{raw_name} failed: {self.last_error}"}

        # Convert MCP TextContent/ImageContent/etc. into a simple shape.
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
        """Exponential-backoff restart up to _MAX_RESTART_ATTEMPTS."""
        if self.restart_attempts >= _MAX_RESTART_ATTEMPTS:
            self.state = HandleState.DEAD
            return
        delay = _RESTART_BACKOFF_S[min(self.restart_attempts, len(_RESTART_BACKOFF_S) - 1)]
        self.restart_attempts += 1
        await asyncio.sleep(delay)
        await self.connect()
