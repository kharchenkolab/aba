"""Unit test for core.exec.stream_coalesce.Coalescer.

Covers: byte-cap flush, interval flush, no-output no-flush, per-stream
independence, final-tail flush, lifetime byte counters.

Run:
    .venv/bin/python tests/d18_stream_coalesce.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.exec.stream_coalesce import Coalescer


def test_byte_cap_flush():
    """A single push that crosses flush_bytes emits exactly one flush."""
    events = []
    c = Coalescer(flush_bytes=100, flush_interval_s=999, on_flush=events.append, now_fn=lambda: 0.0)
    c.push("stdout", "x" * 50)        # under cap → no flush yet
    assert events == [], events
    c.push("stdout", "x" * 60)        # crosses → flush
    assert len(events) == 1, events
    assert events[0]["stream"] == "stdout"
    assert events[0]["text"] == "x" * 110
    assert events[0]["bytes_total"] == 110
    assert events[0]["reason"] == "bytes"
    print("OK byte-cap flush")


def test_interval_flush():
    """Time passing past flush_interval triggers flush on next push."""
    t = [0.0]
    events = []
    c = Coalescer(flush_bytes=10_000, flush_interval_s=1.0,
                  on_flush=events.append, now_fn=lambda: t[0])
    c.push("stdout", "hello")         # small push, no byte-cap fire
    assert events == [], events
    t[0] = 1.5                        # 1.5s elapsed
    c.push("stdout", " world")        # push triggers interval check → flush
    assert len(events) == 1, events
    assert events[0]["text"] == "hello world"
    assert events[0]["reason"] == "interval"
    print("OK interval flush")


def test_no_output_no_flush():
    """No push, no flush. maybe_flush() with empty buffer is a no-op."""
    t = [0.0]
    events = []
    c = Coalescer(flush_bytes=100, flush_interval_s=1.0,
                  on_flush=events.append, now_fn=lambda: t[0])
    t[0] = 5.0
    c.maybe_flush()                   # buffer empty → no event
    assert events == [], events
    print("OK no-output no-flush")


def test_maybe_flush_time_only():
    """maybe_flush() emits pending bytes when time-cap has elapsed."""
    t = [0.0]
    events = []
    c = Coalescer(flush_bytes=10_000, flush_interval_s=1.0,
                  on_flush=events.append, now_fn=lambda: t[0])
    c.push("stdout", "trickle")       # small, no flush
    assert events == [], events
    t[0] = 0.5
    c.maybe_flush()                   # 0.5s elapsed — under cap, no flush
    assert events == [], events
    t[0] = 1.2
    c.maybe_flush()                   # 1.2s elapsed — flush
    assert len(events) == 1, events
    assert events[0]["text"] == "trickle"
    print("OK maybe_flush time-only")


def test_streams_independent():
    """Stdout and stderr flush as separate chunks in a single flush."""
    events = []
    c = Coalescer(flush_bytes=10, flush_interval_s=999,
                  on_flush=events.append, now_fn=lambda: 0.0)
    c.push("stdout", "hi-")           # 3 bytes pending
    c.push("stderr", "WARN ")         # 5 more (8 pending — still under 10)
    c.push("stdout", "world")         # +5 → 13 pending → flush
    assert len(events) == 2, events   # stdout + stderr in same flush, separate events
    by_stream = {e["stream"]: e for e in events}
    assert by_stream["stdout"]["text"] == "hi-world"
    assert by_stream["stderr"]["text"] == "WARN "
    assert by_stream["stdout"]["bytes_total"] == 8
    assert by_stream["stderr"]["bytes_total"] == 5
    print("OK streams independent")


def test_final_flush():
    """Explicit flush() emits any pending bytes — used as the tail flush."""
    events = []
    c = Coalescer(flush_bytes=10_000, flush_interval_s=999,
                  on_flush=events.append, now_fn=lambda: 0.0)
    c.push("stdout", "almost done")
    assert events == [], events
    c.flush(reason="final")
    assert len(events) == 1, events
    assert events[0]["text"] == "almost done"
    assert events[0]["reason"] == "final"
    # Idempotent on empty buffer
    c.flush(reason="final")
    assert len(events) == 1, events
    print("OK final flush + idempotent")


def test_lifetime_counters():
    """bytes_total in each event reflects cumulative per-stream bytes
    across flushes — not just the current chunk."""
    events = []
    c = Coalescer(flush_bytes=5, flush_interval_s=999,
                  on_flush=events.append, now_fn=lambda: 0.0)
    c.push("stdout", "12345")         # 5 bytes → flush
    c.push("stdout", "67890")         # 5 more → flush
    assert len(events) == 2, events
    assert events[0]["bytes_total"] == 5
    assert events[1]["bytes_total"] == 10   # cumulative
    print("OK lifetime counters")


def test_empty_push_noop():
    """push('', '') and push(stream, '') do nothing."""
    events = []
    c = Coalescer(flush_bytes=10, flush_interval_s=999, on_flush=events.append)
    c.push("stdout", "")
    c.push("stderr", "")
    assert events == [], events
    print("OK empty push no-op")


if __name__ == "__main__":
    test_byte_cap_flush()
    test_interval_flush()
    test_no_output_no_flush()
    test_maybe_flush_time_only()
    test_streams_independent()
    test_final_flush()
    test_lifetime_counters()
    test_empty_push_noop()
    print("\nALL OK")
