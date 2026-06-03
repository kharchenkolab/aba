"""Unit test for core.runtime.tool_stream_buffer (#334 Phase 2).

Covers: record + get round-trip, per-stream accumulation, mark_done flips
status + shortens TTL, GC of expired entries, snip_middle applied at read,
absent buffer returns None.

Run:
    .venv/bin/python tests/d18c_tool_stream_buffer.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.runtime import tool_stream_buffer as tsb


def reset():
    tsb._clear_for_tests()


def test_record_and_get():
    reset()
    tsb.record_chunk("run1", "tu1", stream="stdout",
                     text="hello\n", bytes_total=6, elapsed_s=0.5)
    tsb.record_chunk("run1", "tu1", stream="stderr",
                     text="WARN\n", bytes_total=5, elapsed_s=0.6)
    tsb.record_chunk("run1", "tu1", stream="stdout",
                     text="world\n", bytes_total=12, elapsed_s=1.1)
    snap = tsb.get("run1", "tu1")
    assert snap is not None, snap
    assert snap["status"] == "running"
    assert snap["stdout"] == "hello\nworld\n", snap["stdout"]
    assert snap["stderr"] == "WARN\n", snap["stderr"]
    assert snap["bytes_stdout"] == 12
    assert snap["bytes_stderr"] == 5
    assert snap["elapsed_s"] == 1.1
    print("OK record + get + per-stream accumulation")


def test_missing_returns_none():
    reset()
    assert tsb.get("nope", "nope") is None
    print("OK absent buffer returns None")


def test_mark_done_flips_status():
    reset()
    tsb.record_chunk("run2", "tu2", stream="stdout",
                     text="working", bytes_total=7, elapsed_s=0.1)
    snap = tsb.get("run2", "tu2")
    assert snap["status"] == "running"
    tsb.mark_done("run2", "tu2")
    snap = tsb.get("run2", "tu2")
    assert snap["status"] == "done", snap
    # Idempotent
    tsb.mark_done("run2", "tu2")
    print("OK mark_done flips status (idempotent)")


def test_snip_middle_applied():
    """Big buffer gets middle-snipped at read time so the rehydrated view
    matches the eventual tool_result (which uses snip_middle too)."""
    reset()
    big = "X" * 80_000      # >> default 50K cap → must snip
    tsb.record_chunk("run3", "tu3", stream="stdout",
                     text=big, bytes_total=80_000, elapsed_s=2.0)
    snap = tsb.get("run3", "tu3")
    assert snap is not None
    # Snipped output should contain the snip marker
    assert "ABA snipped" in snap["stdout"], snap["stdout"][:200]
    assert len(snap["stdout"]) < 80_000
    # But bytes_stdout still reflects the true total
    assert snap["bytes_stdout"] == 80_000
    print(f"OK snip_middle applied at read (kept {len(snap['stdout'])} of 80000)")


def test_gc_drops_expired():
    """Manually expire a done buffer and verify get() returns None + cleans up."""
    reset()
    tsb.record_chunk("run4", "tu4", stream="stdout",
                     text="x", bytes_total=1, elapsed_s=0)
    tsb.mark_done("run4", "tu4")
    # Force expiration by reaching into the internal state.
    with tsb._lock:
        tsb._buffers[("run4", "tu4")].expires_at = 0
    assert tsb.get("run4", "tu4") is None
    # Confirm it was popped, not just hidden
    with tsb._lock:
        assert ("run4", "tu4") not in tsb._buffers
    print("OK GC drops expired buffers on read")


def test_empty_chunk_ignored():
    reset()
    tsb.record_chunk("run5", "tu5", stream="stdout", text="",
                     bytes_total=0, elapsed_s=0)
    # No-op — empty text shouldn't create a buffer entry
    assert tsb.get("run5", "tu5") is None
    print("OK empty chunk no-op")


def test_multi_tool_isolation():
    """Buffers keyed by (run_id, tool_use_id) — siblings don't collide."""
    reset()
    tsb.record_chunk("run6", "tu_a", stream="stdout", text="A",
                     bytes_total=1, elapsed_s=0)
    tsb.record_chunk("run6", "tu_b", stream="stdout", text="B",
                     bytes_total=1, elapsed_s=0)
    tsb.record_chunk("run7", "tu_a", stream="stdout", text="X",
                     bytes_total=1, elapsed_s=0)
    assert tsb.get("run6", "tu_a")["stdout"] == "A"
    assert tsb.get("run6", "tu_b")["stdout"] == "B"
    assert tsb.get("run7", "tu_a")["stdout"] == "X"
    print("OK multi-tool isolation")


if __name__ == "__main__":
    test_record_and_get()
    test_missing_returns_none()
    test_mark_done_flips_status()
    test_snip_middle_applied()
    test_gc_drops_expired()
    test_empty_chunk_ignored()
    test_multi_tool_isolation()
    print("\nALL OK")
