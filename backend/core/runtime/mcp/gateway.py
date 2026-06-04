"""MCP gateway — owns a background asyncio loop in a daemon thread.

Sync callers (guide.py's execute_tool, /api/admin/mcp endpoint) submit
coroutines to the loop via asyncio.run_coroutine_threadsafe and block on
the resulting Future.

The loop owns all ServerHandle state — connect / call / shutdown all run
inside it. No locks needed across the sync/async boundary beyond what
run_coroutine_threadsafe provides.
"""
from __future__ import annotations
import asyncio
import concurrent.futures
import threading
from pathlib import Path
from typing import Any, Optional

from .config import ServerConfig, load as load_config
from .server_handle import ServerHandle, ToolInfo, HandleState


# Background loop state. Initialized lazily on first start_all() call.
_loop: Optional[asyncio.AbstractEventLoop] = None
_thread: Optional[threading.Thread] = None
_handles: dict[str, ServerHandle] = {}     # config.name → handle
_started = False


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """Spin up the background event loop on first use. Idempotent."""
    global _loop, _thread
    if _loop is not None:
        return _loop
    loop = asyncio.new_event_loop()

    def run():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=run, name="aba-mcp-gateway", daemon=True)
    t.start()
    _loop = loop
    _thread = t
    return loop


def _submit(coro, cancel_token=None):
    """Run a coroutine on the background loop and wait for its result.
    When a cancel_token is supplied, register fut.cancel as an
    interrupter — Stop will cancel the underlying asyncio task, which
    surfaces as CancelledError here and gets translated to a structured
    cancelled status the model can react to.

    Forward-looking: this is the seam every MCP-served tool flows
    through. When the protocol's cancellation notification matures,
    add `session.send_notification('cancelled', ...)` inside the
    interrupter; the call signature here doesn't change."""
    loop = _ensure_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    unregister = None
    if cancel_token is not None:
        unregister = cancel_token.register(lambda: fut.cancel())
    try:
        return fut.result()
    except (asyncio.CancelledError, concurrent.futures.CancelledError):
        return {"status": "cancelled",
                "note": f"MCP call cancelled by user "
                        f"({cancel_token.reason if cancel_token else 'cancelled'})."}
    finally:
        if unregister is not None:
            unregister()


def start_all(config_path: Optional[Path] = None) -> None:
    """Load servers.yaml + connect every enabled server. Idempotent —
    a second call no-ops if already started. Errors from individual
    server connects are surfaced in status() but never block startup."""
    global _started
    if _started:
        return
    cfgs = load_config(config_path) if config_path else []
    for c in cfgs:
        if not c.enabled:
            continue
        _handles[c.name] = ServerHandle(config=c)
    if _handles:
        _submit(_connect_all())
    _started = True


async def _connect_all() -> None:
    await asyncio.gather(*(h.connect() for h in _handles.values()),
                         return_exceptions=True)


def register_inprocess_server(name: str, server_factory, default_timeout_s: int = 30) -> dict:
    """Adopt an IN-PROCESS MCP server (memory transport) — Phase 6 hook.
    `server_factory` is a zero-arg callable that returns a fresh FastMCP
    server (called on every (re)connect). Idempotent on name. Returns
    {status, server, tools|note} once the connect attempt settles.

    Used by content (bio) at startup to expose its tool catalogue
    through the same channel as external stdio servers, without
    spawning a subprocess."""
    from .in_process import InProcessServerHandle, _InProcessConfigShim
    global _started
    existing = _handles.get(name)
    if existing is not None and existing.state == HandleState.CONNECTED:
        return {"status": "already_connected", "server": name,
                "tools": [t.name for t in existing.tools]}
    h = existing if existing is not None else InProcessServerHandle(
        server_factory=server_factory,
        config=_InProcessConfigShim(name=name, default_timeout_s=default_timeout_s),
    )
    _handles[name] = h
    _started = True
    # Memory-transport connect needs the gateway loop running.
    _ensure_loop()
    _submit(h.connect())
    if h.state == HandleState.CONNECTED:
        return {"status": "connected", "server": name,
                "tools": [t.name for t in h.tools]}
    return {"status": "error", "server": name,
            "note": h.last_error or "in-process connect failed"}


def add_server(cfg: ServerConfig) -> dict:
    """Adopt + connect a server at RUNTIME (not from servers.yaml) — the
    materialization path for an mcp_server-archetype capability. Idempotent on
    name: an already-connected server short-circuits; a present-but-disconnected
    one is reconnected. Returns {status, server, tools|note}. Synchronous: blocks
    on the background loop until the connect attempt settles."""
    global _started
    existing = _handles.get(cfg.name)
    if existing is not None and existing.state == HandleState.CONNECTED:
        return {"status": "already_connected", "server": cfg.name,
                "tools": [t.name for t in existing.tools]}
    h = existing if existing is not None else ServerHandle(config=cfg)
    _handles[cfg.name] = h
    _started = True
    _submit(h.connect())
    if h.state == HandleState.CONNECTED:
        return {"status": "connected", "server": cfg.name,
                "tools": [t.name for t in h.tools]}
    return {"status": "error", "server": cfg.name,
            "note": h.last_error or "connect failed"}


def shutdown() -> None:
    """Tear down all servers + stop the background loop."""
    global _loop, _thread, _started
    if not _started:
        return
    try:
        _submit(asyncio.gather(*(h.shutdown() for h in _handles.values()),
                               return_exceptions=True))
    except Exception:
        pass
    if _loop:
        _loop.call_soon_threadsafe(_loop.stop)
    _handles.clear()
    _started = False
    _loop = None
    _thread = None


def list_tools() -> list[dict[str, Any]]:
    """Tool schemas in Anthropic wire shape (name, description,
    input_schema), one entry per MCP-exposed tool across all CONNECTED
    servers. Disconnected/dead servers contribute nothing.

    Handles with `expose_in_catalog=False` (the in-process aba_core
    during Phase 6 migration) are SKIPPED — their tools are already
    advertised via TOOL_SCHEMAS and dispatched via `is_inprocess_tool`."""
    out: list[dict[str, Any]] = []
    for h in _handles.values():
        if h.state != HandleState.CONNECTED:
            continue
        if not getattr(h, "expose_in_catalog", True):
            continue
        for t in h.tools:
            out.append({
                "name":         t.name,
                "description":  t.description,
                "input_schema": t.input_schema,
            })
    return out


def is_inprocess_tool(raw_name: str, server: str = "aba_core") -> bool:
    """Phase 6 dispatcher hook — returns True iff `{server}:{raw_name}`
    is a registered tool on the named in-process server. The bio
    dispatcher consults this for every tool call so migrated tools
    route through MCP even though their bare name (no prefix) is what
    the agent uses. Returns False when the server isn't registered, is
    disconnected, or doesn't have a tool by that name."""
    h = _handles.get(server)
    if h is None or h.state != HandleState.CONNECTED:
        return False
    full = f"{server}:{raw_name}"
    return any(t.name == full for t in h.tools)


def is_mcp_tool(name: str) -> bool:
    """True iff `name` is a prefixed MCP tool registered with the gateway."""
    if ":" not in name:
        return False
    server, _ = name.split(":", 1)
    h = _handles.get(server)
    return bool(h and h.state == HandleState.CONNECTED
                and any(t.name == name for t in h.tools))


def call(name: str, arguments: dict, timeout_s: Optional[int] = None,
         cancel_token=None) -> dict:
    """Sync dispatch from execute_tool. Blocks on the background loop.
    cancel_token (optional) — if the user hits Stop, the underlying
    asyncio task is cancelled; this returns a {status:'cancelled'}
    result that the model can react to."""
    if ":" not in name:
        return {"status": "error", "note": f"MCP tool names must be 'server:tool'; got {name!r}"}
    server, raw = name.split(":", 1)
    h = _handles.get(server)
    if h is None:
        return {"status": "error", "note": f"No MCP server named {server!r}"}
    return _submit(h.call_tool(raw, arguments, timeout_s=timeout_s),
                   cancel_token=cancel_token)


def status() -> dict[str, Any]:
    """Admin: per-server health + tool counts + last error."""
    return {
        "started": _started,
        "servers": [
            {
                "name":   h.config.name,
                "state":  h.state.value,
                "tools":  len(h.tools),
                "last_error": h.last_error,
                "restart_attempts": h.restart_attempts,
            }
            for h in _handles.values()
        ],
    }


# ---- testing hooks ----

def _reset_for_testing() -> None:
    """Tear down + clear state between tests."""
    shutdown()
