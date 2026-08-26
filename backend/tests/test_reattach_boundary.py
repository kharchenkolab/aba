"""The replay/live boundary has to be a MARKER, not a guess.

Switching into a thread with a turn in flight rebuilds that turn by replaying
its event log — mid-turn assistant output lives nowhere else. Replaying it
visibly re-animates work already watched: the tool the turn is running appears
to start again, every time you look. Reported live twice on 2026-08-26.

The client fixes that by folding the backlog into state without rendering, then
rendering once. That needs a deterministic end to the backlog. "No events for a
while" is NOT one — a turn mid-install is legitimately silent for minutes, and
a timer would either cut the backlog short or hold the render past it.
"""
import asyncio

import pytest

from core.runtime import turn_sink as ts
from core.runtime import wire


def _drain(sink, since=0, expect=3):
    async def run():
        got = []
        agen = ts.stream_from_sink(sink, since=since)
        for _ in range(expect):
            got.append(await agen.__anext__())
        await agen.aclose()
        return got
    return asyncio.run(run())


_ids = iter(range(1000))


def _mk(n_events: int):
    # a UNIQUE run per call: ts.create is idempotent on run_id, so two tests
    # sharing an id share a sink and its seq counter
    sink = ts.create(f"run_{next(_ids)}", "thr_x", "2026-08-26T00:00:00Z")
    for i in range(n_events):
        # built through the wire builder, like production — a fixture that
        # constructs payloads more loosely than the real producer is a fake
        # that blesses shapes the transport would reject
        sink.push(wire.delta(text=f"t{i}"))
    return sink


def test_the_backlog_ends_with_a_named_marker():
    sink = _mk(3)
    frames = _drain(sink, since=0, expect=4)
    assert '"type": "caught_up"' in frames[-1] or "'caught_up'" in frames[-1], \
        frames[-1]
    assert '"replayed": 3' in frames[-1], frames[-1]


def test_the_marker_arrives_after_the_replay_not_before():
    """Order is the whole contract: a marker before the backlog would render an
    empty turn and then animate it anyway."""
    sink = _mk(2)
    frames = _drain(sink, since=0, expect=3)
    assert "t0" in frames[0] and "t1" in frames[1]
    assert "caught_up" in frames[2]


def test_a_reattach_with_nothing_to_replay_says_replayed_zero():
    """WIDE: the client uses this to skip the extra render entirely, so it must
    be present and honest, not omitted."""
    sink = _mk(2)
    frames = _drain(sink, since=99, expect=1)   # since past the tail
    assert "caught_up" in frames[0] and '"replayed": 0' in frames[0]


def test_the_marker_carries_the_last_replayed_seq():
    """A client tracking lastSeq must be able to apply it idempotently — a
    marker with a bogus seq would rewind or skip the reattach point."""
    sink = _mk(3)
    frames = _drain(sink, since=0, expect=4)
    import re
    seqs = [int(re.search(r'"seq":\s*(\d+)', f).group(1)) for f in frames]
    assert seqs[-1] == seqs[-2], (seqs, "marker seq must equal the last event's")
