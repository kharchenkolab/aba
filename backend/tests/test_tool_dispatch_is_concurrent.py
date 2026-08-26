"""One slow tool must not block every other tool in the process.

Live 2026-08-26: a `Skill` call — a recipe file read, 4 ms and 5 ms in other
turns — took 349 SECONDS, finishing within seconds of two concurrent
`ensure_capability` calls (356 s, 363 s). Three threads, three turns, and the
trivial one waited for the expensive ones. The user's first question in a fresh
project took ten minutes to appear.

The cause is still unidentified. These guards exist because the investigation
ELIMINATED two prime suspects by measurement, and both are load-bearing enough
that a future change could reintroduce the problem here without anyone noticing:

  * the gateway's single shared background loop (every tool call is
    `run_coroutine_threadsafe` onto ONE loop — an obvious serialization point,
    and it is not one, because the calls yield)
  * FastMCP's handling of SYNC tools (all 14 bio tools in discovery.py are
    plain `def`; if FastMCP awaited them inline on its loop, one blocking tool
    would freeze the server — it offloads them instead)

If either becomes serial, these fail. What they do NOT do is prove the live
problem is gone: it lives somewhere these do not reach.
"""
import threading
import time

import pytest

pytestmark = pytest.mark.platform

SLOW_S = 2.0


def test_the_gateway_does_not_serialize_tool_calls():
    """A trivial call issued while a slow one is in flight must return first."""
    from core.runtime.mcp import gateway

    class _H:
        config = type("C", (), {"name": "t", "default_timeout_s": 60})()
        state = None
        tools: list = []
        last_error = None
        restart_attempts = 0

        async def call_tool(self, raw, args, timeout_s=None):
            import asyncio
            if raw == "slow":
                await asyncio.to_thread(time.sleep, SLOW_S)
            return {"status": "ok"}

    saved, saved_started = dict(gateway._handles), gateway._started
    gateway._handles["t"] = _H()
    gateway._started = True
    try:
        out = {}

        def call(label, tool):
            t0 = time.time()
            gateway.call(f"t:{tool}", {})
            out[label] = time.time() - t0

        slow = threading.Thread(target=call, args=("slow", "slow"))
        slow.start()
        time.sleep(0.3)                      # ensure it is genuinely in flight
        fast = threading.Thread(target=call, args=("fast", "fast"))
        fast.start()
        slow.join(); fast.join()

        assert out["slow"] >= SLOW_S * 0.8, "the slow call did not actually run"
        assert out["fast"] < SLOW_S * 0.5, (
            f"the trivial call waited {out['fast']:.1f}s for the slow one — "
            f"tool dispatch is serialized")
    finally:
        gateway._handles.clear(); gateway._handles.update(saved)
        gateway._started = saved_started


def test_fastmcp_runs_sync_tools_off_its_event_loop():
    """Every bio tool is a sync `def`. If FastMCP awaited them inline, one
    blocking install would freeze every other turn in the process."""
    anyio = pytest.importorskip("anyio")
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("t")

    @mcp.tool()
    def slow() -> str:
        time.sleep(SLOW_S)          # BLOCKING, like a real install
        return "slow"

    @mcp.tool()
    def fast() -> str:
        return "fast"

    async def main():
        from mcp.shared.memory import (
            create_connected_server_and_client_session as conn)
        async with conn(mcp._mcp_server) as client:
            out = {}

            async def go(name):
                s = time.time()
                await client.call_tool(name, {})
                out[name] = time.time() - s

            async with anyio.create_task_group() as tg:
                tg.start_soon(go, "slow")
                await anyio.sleep(0.3)
                tg.start_soon(go, "fast")
            return out

    out = anyio.run(main)
    assert out["slow"] >= SLOW_S * 0.8
    assert out["fast"] < SLOW_S * 0.5, (
        f"a sync tool blocked the server for {out['fast']:.1f}s — FastMCP is "
        f"running sync tools on its event loop")
