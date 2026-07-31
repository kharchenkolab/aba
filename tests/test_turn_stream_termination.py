"""Every SSE subscriber must terminate — including one that attaches late.

`TurnSink.close()` hands the `None` sentinel to the subscribers that exist AT
THAT MOMENT. A subscriber attaching afterwards used to wait on a queue nothing
would ever push to: `stream_from_sink` replayed the tail, then heartbeated
forever, and the client never learned the turn was over.

The window is between `start_turn()` and the response generator's first step.
For one turn it is small; under load it is not.

Live (2026-07-27), driving three concurrent turns per lane against one server:
every turn completed and wrote its messages, and then all six SSE connections sat
ESTABLISHED and idle for the full client timeout. In a browser that is a tab
whose turn never finishes — the events all arrived, the stream just never ended.

Found by the concurrency lane (`regtest/live/workflows.py --concurrent`), which
exists because a scenario that drives ONE thread to completion cannot see it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime import turn_sink as ts  # noqa: E402

DONE = {"type": "done", "run_id": "r", "status": "ok"}
TEXT = {"type": "text", "text": "hello"}


async def _drain(sink, *, since: int = 0, timeout: float = 3.0) -> list:
    """Consume the stream to completion. Raises TimeoutError if it never ends —
    which IS the failure this file is about, so it must not be swallowed."""
    out: list = []

    async def go():
        async for frame in ts.stream_from_sink(sink, since=since):
            out.append(frame)

    await asyncio.wait_for(go(), timeout=timeout)
    return out


def _run(coro):
    return asyncio.run(coro)


# ── the regression ───────────────────────────────────────────────────────────

def test_a_subscriber_that_attaches_after_close_terminates():
    """THE bug: the turn is over before the client starts reading."""
    async def main():
        sink = ts.create("run_late", "thr_late", "now")
        sink.push(TEXT)
        sink.push(DONE)
        sink.close()
        frames = await _drain(sink)
        # It must also still RECEIVE the events, not just exit: a stream that
        # terminates by dropping the turn's output is a different bug.
        assert any("hello" in f for f in frames), frames
    _run(main())


def test_a_subscriber_attaching_after_close_with_since_at_the_end_terminates():
    """WIDE — the degenerate replay: a reattach whose `since` is already the
    last seq replays NOTHING, so the generator goes straight to the queue with
    no frames to mask the hang."""
    async def main():
        sink = ts.create("run_late2", "thr_late2", "now")
        sink.push(TEXT)
        sink.close()
        frames = await _drain(sink, since=sink.last_seq)
        assert frames == [] or all(isinstance(f, str) for f in frames)
    _run(main())


def test_an_empty_closed_sink_terminates():
    """WIDE — nothing was ever pushed (a turn that died before its first
    event). Nothing to replay, and close() already fired."""
    async def main():
        sink = ts.create("run_empty", "thr_empty", "now")
        sink.close()
        await _drain(sink)
    _run(main())


def test_many_late_subscribers_all_terminate():
    """The live shape: several readers on a finished turn, e.g. the original
    request plus reattaching tabs. One shared sentinel would only free one."""
    async def main():
        sink = ts.create("run_many", "thr_many", "now")
        sink.push(TEXT)
        sink.close()
        await asyncio.gather(*[_drain(sink) for _ in range(4)])
    _run(main())


# ── ceilings: the ordinary lifecycle must be untouched ───────────────────────

def test_the_normal_order_still_streams_then_ends():
    """CEILING: subscriber first, events, then close. Over-applying the fix
    (e.g. sentinel on every subscribe) would end every live stream instantly."""
    async def main():
        sink = ts.create("run_norm", "thr_norm", "now")
        out: list = []

        async def read():
            async for frame in ts.stream_from_sink(sink, since=0):
                out.append(frame)

        task = asyncio.create_task(read())
        await asyncio.sleep(0.05)
        sink.push(TEXT)
        await asyncio.sleep(0.05)
        assert not task.done(), "the stream ended while the turn was still live"
        sink.push(DONE)
        sink.close()
        await asyncio.wait_for(task, timeout=3)
        assert any("hello" in f for f in out), out
    _run(main())


def test_an_open_sink_does_not_terminate_early():
    """CEILING, stated as the forbidden outcome rather than the happy one: a
    subscriber on a LIVE turn must still be waiting after the heartbeat path
    has had a chance to run."""
    async def main():
        sink = ts.create("run_open", "thr_open", "now")
        out: list = []

        async def read():
            async for frame in ts.stream_from_sink(sink, since=0):
                out.append(frame)

        task = asyncio.create_task(read())
        await asyncio.sleep(0.2)
        assert not task.done(), "a live turn's stream terminated on its own"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _run(main())


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
