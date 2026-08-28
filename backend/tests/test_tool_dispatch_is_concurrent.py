"""One slow tool must not block every other tool in the process.

Live 2026-08-26 → root-caused 2026-08-28: a `Skill` call (4 ms in other turns)
took 349 s beside two concurrent `ensure_capability` calls; a one-line
run_python took 93 s beside a 91 s install, unblocking when the installing
FUNCTION returned. The mechanism: every aba_core tool is a sync `def`, fastmcp
runs a sync tool INLINE on the loop serving it, and all in-process servers
share the ONE gateway loop — so one long tool body starved every other tool
call in the process. The fix is `core.runtime.mcp.offload` (sync bodies →
worker threads), applied at the in-process connect seam.

THE PREVIOUS VERSION OF THIS FILE WAS GREEN THROUGH THE LIVE BUG — both named
failure modes at once. Its fastmcp test timed the trivial call with clocks that
ran ON the loop under test (a blocked loop freezes the `anyio.sleep` launch gate
AND the timer with it, so the measured duration stays small), and its gateway
test's fake handler wrapped the block in `asyncio.to_thread` — yielding where
the real path did not. The re-cut rules, so it cannot happen again: every clock
lives on an OS thread the loop cannot touch, and the slow body must PROVE it
has entered (threading.Event) before the trivial call is issued.
"""
import asyncio
import threading
import time

import pytest

pytestmark = pytest.mark.platform

SLOW_S = 1.5
BUDGET_S = 0.5 * SLOW_S


def _mini_server(slow_started: threading.Event,
                 slow_release: threading.Event,
                 body_threads: dict):
    """A FastMCP server with the shapes under test — all sync `def`, like
    every real aba_core tool."""
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("mini")

    @mcp.tool()
    def slow() -> str:
        body_threads["slow"] = threading.current_thread().name
        slow_started.set()
        slow_release.wait(SLOW_S)      # blocking body, interruptible by test
        return "slow-done"

    @mcp.tool()
    def fast() -> str:
        body_threads["fast"] = threading.current_thread().name
        return "fast-done"

    @mcp.tool()
    def boom() -> str:
        raise RuntimeError("intentional")

    return mcp


@pytest.fixture()
def gateway_mini():
    """The REAL seam: register the mini server through the gateway's
    in-process door (offload applied at connect), yield a caller, restore."""
    from core.runtime.mcp import gateway
    slow_started, slow_release = threading.Event(), threading.Event()
    body_threads: dict = {}
    saved, saved_started = dict(gateway._handles), gateway._started
    try:
        out = gateway.register_inprocess_server(
            "mini", lambda: _mini_server(slow_started, slow_release, body_threads))
        assert out.get("status") in ("connected", "already_connected"), out
        yield gateway, slow_started, slow_release, body_threads
    finally:
        h = gateway._handles.pop("mini", None)
        if h is not None:
            try:
                gateway._submit(h.shutdown())
            except Exception:  # noqa: BLE001
                pass
        gateway._handles.clear()
        gateway._handles.update(saved)
        gateway._started = saved_started


def test_a_blocking_sync_tool_does_not_starve_a_sibling(gateway_mini):
    """THE guard for the incident class, on the production path: gateway →
    memory transport → fastmcp dispatch → offloaded sync body. Clocks on OS
    threads; the trivial call issued only after the slow BODY has entered."""
    gateway, slow_started, slow_release, body_threads = gateway_mini
    res: dict = {}

    def call(name):
        t0 = time.time()
        r = gateway.call(f"mini:{name}", {})
        res[name] = (round(time.time() - t0, 2), r)

    ta = threading.Thread(target=call, args=("slow",))
    ta.start()
    assert slow_started.wait(5), "slow body never entered — measured nothing"
    tb = threading.Thread(target=call, args=("fast",))
    tb.start()
    tb.join(timeout=SLOW_S + 5)
    assert not tb.is_alive(), "fast call still blocked after slow's full span"
    fast_s, fast_r = res["fast"]
    # ARMED both ways: the slow body was mid-flight when fast returned…
    assert not res.get("slow"), "slow finished before fast measured — vacuous"
    assert fast_s < BUDGET_S, (
        f"a trivial tool waited {fast_s}s behind a blocking sync body — "
        f"the gateway loop is serialized again (offload broken?)")
    slow_release.set()
    ta.join(timeout=10)
    assert res["slow"][0] >= 0.0 and "slow-done" in str(res["slow"][1])
    assert "fast-done" in str(fast_r)


def test_sync_bodies_run_off_the_gateway_loop(gateway_mini):
    """The forbidden ACTION, asserted directly: no sync tool body may execute
    on the gateway loop thread (that is the exact mechanism of the freeze —
    checking output timings alone let a permissive fake bless it before)."""
    gateway, slow_started, slow_release, body_threads = gateway_mini
    slow_release.set()                     # don't linger
    gateway.call("mini:fast", {})
    gateway.call("mini:slow", {})
    loop_thread = gateway._thread.name
    for name, tname in body_threads.items():
        assert tname != loop_thread, (
            f"tool body {name!r} ran ON the gateway loop thread {tname!r}")


def test_offload_preserves_the_calling_contract():
    """The wrap must change WHERE bodies run, never WHAT the tools accept:
    schema (params/required/types) byte-identical before and after."""
    from core.runtime.mcp.offload import offload_sync_tools
    s1 = _mini_server(threading.Event(), threading.Event(), {})
    s2 = _mini_server(threading.Event(), threading.Event(), {})
    before = {n: t.parameters for n, t in s1._tool_manager._tools.items()}
    n = offload_sync_tools(s2)
    assert n >= 3, f"offload wrapped {n} tools — the mini server has 3 sync"
    after = {n_: t.parameters for n_, t in s2._tool_manager._tools.items()}
    assert before == after
    # and an async tool passes through untouched
    from mcp.server.fastmcp import FastMCP
    s3 = FastMCP("a")

    @s3.tool()
    async def already_async() -> str:
        return "ok"

    fn_before = s3._tool_manager._tools["already_async"].fn
    offload_sync_tools(s3)
    assert s3._tool_manager._tools["already_async"].fn is fn_before


def test_errors_still_surface_through_the_wrap(gateway_mini):
    """A raising sync body must produce the same error envelope, not vanish
    into the worker thread."""
    gateway, _st, _rel, _bt = gateway_mini
    r = gateway.call("mini:boom", {})
    text = str(r)
    assert "intentional" in text or "error" in text.lower(), r


def test_offload_fails_loud_on_unknown_internals():
    """A fastmcp bump that moves the internals must fail the connect, never
    silently serve a loop-blocking catalog (ARMED: the no-op is the bug)."""
    from core.runtime.mcp.offload import offload_sync_tools

    class _NotAServer:
        pass

    with pytest.raises(AttributeError):
        offload_sync_tools(_NotAServer())
