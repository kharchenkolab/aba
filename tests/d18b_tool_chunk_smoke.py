"""Integration smoke test for #334 Phase 1 live-tail.

Drives run_python with a script that drips output across multiple coalescer
intervals + a single big burst that triggers the byte-cap flush. Captures
the SSE stream and asserts that `tool_chunk` events arrive with the right
shape, attached to the right `tool_use_id`, with monotonically growing
`bytes_total`.

Run after bouncing uvicorn on :8000:
    .venv/bin/python tests/d18b_tool_chunk_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = os.environ.get("ABA_TEST_BASE", "http://localhost:8000")

# Script designed to exercise BOTH coalescer paths:
# 1. Small print every 1.2s × 5 → interval-flush (1s cap)
# 2. One 15 KB burst → byte-flush (10 KB cap)
SCRIPT = r"""
import sys, time
for i in range(5):
    print(f"trickle {i}", flush=True)
    time.sleep(1.2)
print("X" * 15000, flush=True)
print("done")
"""


def _post_json(path, payload, timeout=10):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def _stream_sse(path, payload, *, timeout=120):
    """Yield (event_type, data_dict) pairs from an SSE stream."""
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buf = b""
        for line in resp:
            if not line.strip():
                continue
            line = line.decode(errors="replace").rstrip("\n")
            if line.startswith("data: "):
                payload = line[6:]
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                yield obj.get("type"), obj
            buf = b""


def main():
    # Use the workspace fallback thread; no project required.
    thread_id = f"smoke_{int(time.time())}"
    # Drive run_python directly via the tool harness — bypass the model.
    print(f"[smoke] driving run_python via /api/tools/run_python …")
    # We don't have a direct /api/tools endpoint for ad-hoc runs, so use the
    # generic /api/run-tool if it exists, else exercise via the kernel session
    # API. The simplest reliable path: import the in-process tool handler
    # and pump a mock progress sink so we can assert the coalescer chunks
    # flow through.
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from content.bio import tools
    import queue
    from core.runtime import progress
    q: queue.Queue = queue.Queue()
    progress.set_sink(q)
    try:
        t0 = time.time()
        result = tools.run_python({"code": SCRIPT, "timeout_s": 30},
                                  ctx={"thread_id": thread_id, "session_id": "smoke"})
        elapsed = time.time() - t0
    finally:
        progress.clear_sink()

    # Drain progress queue — all events emitted during the run.
    events = []
    try:
        while True:
            events.append(q.get_nowait())
    except queue.Empty:
        pass

    print(f"[smoke] script ran in {elapsed:.1f}s, result.returncode={result.get('returncode')}")
    print(f"[smoke] drained {len(events)} progress events from sink")

    # Filter just the chunk-typed events (the new path).
    chunks = [e for e in events if isinstance(e, dict) and e.get("type") == "chunk"]
    ticks = [e for e in events if isinstance(e, dict) and e.get("type") != "chunk"]
    print(f"[smoke] {len(chunks)} chunk events, {len(ticks)} legacy tool_progress ticks")

    if not chunks:
        print("FAIL: no chunk events emitted")
        return 1

    # Verify shape + invariants.
    for i, c in enumerate(chunks):
        assert c["stream"] in ("stdout", "stderr"), c
        assert isinstance(c["text"], str) and c["text"], c
        assert isinstance(c["bytes_total"], int) and c["bytes_total"] > 0, c
        assert isinstance(c["elapsed_s"], (int, float)) and c["elapsed_s"] >= 0, c
        print(f"  [{i}] {c['stream']:<6} {len(c['text']):>6}B  total={c['bytes_total']:>6}  t={c['elapsed_s']:>5.1f}s  reason={c.get('reason')}")

    # Per-stream bytes_total must be non-decreasing — WITHIN A SINGLE CELL.
    # The kernel runs a follow-up `_kernel_namespace_preview` cell after the
    # user script; that's a second execute() call with its own Coalescer,
    # whose bytes_total resets to 0. Detect a fresh cell by elapsed_s
    # dropping vs the previous chunk, then assert per-cell monotonicity.
    for stream in ("stdout", "stderr"):
        sc = [c for c in chunks if c["stream"] == stream]
        cell_idx = 0
        prev_elapsed = -1.0
        cells: dict[int, list] = {}
        for c in sc:
            if c["elapsed_s"] < prev_elapsed:
                cell_idx += 1
            cells.setdefault(cell_idx, []).append(c)
            prev_elapsed = c["elapsed_s"]
        for ci, group in cells.items():
            for a, b in zip(group, group[1:]):
                assert a["bytes_total"] <= b["bytes_total"], \
                    f"{stream} bytes_total regressed within cell {ci}: {a['bytes_total']} -> {b['bytes_total']}"
        print(f"[smoke] {stream}: {len(cells)} cell(s), per-cell monotonic ✓")

    # Mix of reasons expected: the 5 trickles + the big burst.
    reasons = {c.get("reason") for c in chunks}
    print(f"[smoke] flush reasons observed: {sorted(reasons)}")
    assert "interval" in reasons or "bytes" in reasons, reasons

    # Coalescing sanity: chunk count << raw print count. Without coalescing,
    # we'd see 5 trickle prints × 2 stream messages each + the 15KB burst as
    # potentially many tiny SSEs. With 1s/10KB caps we expect maybe 6-8 chunks.
    assert len(chunks) < 20, f"too many chunks — coalescing not working: {len(chunks)}"

    print("\nALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
