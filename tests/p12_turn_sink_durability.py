"""C-2 (misc/durable_turns_plan.md §C-2): TurnSink disk persistence +
TTL sweeper.

What durability buys:
  - A Turn that completed before the current process started can still
    be replayed (one-shot) via the SSE reattach endpoint — the in-memory
    sink is gone but the JSONL on disk has every event.
  - A subscriber that reattaches with `since=N` where N predates the
    in-memory tail (MAX_TAIL = 1000 events) gets the gap filled from
    disk, then the in-memory tail for the rest.
  - Old JSONLs and stale closed sinks don't accumulate forever — the
    TTL sweeper cleans both on a 1h timer.

Tests use a private ABA_RUNTIME_DIR so they don't touch real artifacts.

Run:
    .venv/bin/python tests/p12_turn_sink_durability.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="aba_p12_"))
os.environ["ABA_RUNTIME_DIR"] = str(_TMP)
# core.config reads ABA_RUNTIME_DIR at import — must be set FIRST.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _fresh():
    """Clear the registry between tests; remove any JSONLs in the
    per-test runtime dir."""
    from core.runtime import turn_sink as _ts
    _ts._REGISTRY.clear()
    d = _ts._turn_events_dir()
    for p in d.glob("*.jsonl"):
        p.unlink()


def test_push_writes_jsonl_to_disk():
    """Every push() appends a JSON line to turn_events/<run_id>.jsonl."""
    _fresh()
    from core.runtime import turn_sink as _ts
    s = _ts.create("run_test1", thread_id="t", started_at="now")
    s.push({"type": "delta", "text": "hi"})
    s.push({"type": "tool_start", "name": "run_python"})
    s.push({"type": "tool_result", "result": {"returncode": 0}})
    s.close()

    path = _ts._jsonl_path("run_test1")
    assert path.exists(), f"jsonl not written: {path}"
    lines = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 3, f"expected 3 lines, got {len(lines)}"
    assert lines[0] == {"seq": 1, "payload": {"type": "delta", "text": "hi"}}
    assert lines[2]["payload"]["type"] == "tool_result"


def test_rehydrate_reads_all_events_from_disk():
    """rehydrate(run_id) reads the JSONL back into (seq, payload) tuples.
    This is the path used by /api/turns/{rid}/stream when the in-memory
    sink isn't present (post-restart or post-eviction)."""
    _fresh()
    from core.runtime import turn_sink as _ts
    s = _ts.create("run_test2", thread_id="t", started_at="now")
    for i in range(5):
        s.push({"type": "delta", "i": i})
    s.close()
    # Drop from registry to simulate post-restart.
    _ts._REGISTRY.clear()

    events = _ts.rehydrate("run_test2")
    assert len(events) == 5, f"expected 5 events, got {len(events)}"
    seqs = [e[0] for e in events]
    assert seqs == [1, 2, 3, 4, 5], seqs
    assert events[0][1]["i"] == 0
    assert events[4][1]["i"] == 4


def test_rehydrate_honors_since():
    """rehydrate(..., since=N) skips events with seq <= N."""
    _fresh()
    from core.runtime import turn_sink as _ts
    s = _ts.create("run_test3", thread_id="t", started_at="now")
    for i in range(10):
        s.push({"type": "x", "i": i})
    s.close()

    events = _ts.rehydrate("run_test3", since=7)
    assert [e[0] for e in events] == [8, 9, 10]


def test_replay_since_falls_back_to_disk_when_tail_rolled():
    """If a subscriber's `since` predates the in-memory tail's earliest
    seq (because MAX_TAIL rolled), replay_since stitches the gap from
    disk + appends the tail."""
    _fresh()
    from core.runtime import turn_sink as _ts
    # Shrink MAX_TAIL for the test so we don't need 1001 events.
    saved_max = _ts.MAX_TAIL
    _ts.MAX_TAIL = 5
    try:
        s = _ts.create("run_test4", thread_id="t", started_at="now")
        # Replace the deque with one that respects our small MAX_TAIL.
        # (The original was created with maxlen=1000.)
        from collections import deque
        s._tail = deque(maxlen=5)
        for i in range(12):
            s.push({"type": "x", "i": i})
        # In-memory tail now has seq 8..12 (5 entries); seq 1..7 only on disk.

        events = s.replay_since(2)   # ask for everything past seq 2 → seqs 3-12
        seqs = [e[0] for e in events]
        assert seqs == [3, 4, 5, 6, 7, 8, 9, 10, 11, 12], seqs
        # Verify the disk-half came back as the right payloads
        assert events[0][1]["i"] == 2     # seq 3 carries i=2
        assert events[-1][1]["i"] == 11   # seq 12 carries i=11
    finally:
        _ts.MAX_TAIL = saved_max


def test_sweep_deletes_old_jsonls():
    """sweep_once removes JSONL files older than TURN_EVENTS_TTL_S
    and evicts closed sinks older than CLOSED_SINK_TTL_S."""
    _fresh()
    from core.runtime import turn_sink as _ts

    # Stale JSONL: write one + backdate its mtime.
    s = _ts.create("run_stale", thread_id="t", started_at="now")
    s.push({"type": "x"})
    s.close()
    path = _ts._jsonl_path("run_stale")
    # Set mtime to 8 days ago.
    eight_days_ago = time.time() - 8 * 24 * 3600
    os.utime(path, (eight_days_ago, eight_days_ago))

    # Fresh JSONL: don't touch mtime.
    s2 = _ts.create("run_fresh", thread_id="t", started_at="now")
    s2.push({"type": "x"})
    s2.close()

    # Sweep at "now"; stale gets deleted, fresh survives.
    result = _ts.sweep_once()
    assert result["files_deleted"] >= 1, result
    assert not _ts._jsonl_path("run_stale").exists()
    assert _ts._jsonl_path("run_fresh").exists()


def test_sweep_evicts_stale_closed_sinks():
    """Closed sinks older than CLOSED_SINK_TTL_S leave the registry.
    Their JSONLs survive (TURN_EVENTS_TTL_S is longer)."""
    _fresh()
    from core.runtime import turn_sink as _ts

    s = _ts.create("run_old_closed", thread_id="t", started_at="now")
    s.push({"type": "x"})
    s.close()
    # Force closed_at to 2h ago (CLOSED_SINK_TTL_S = 3600).
    s._closed_at = time.time() - 7200

    # And a closed-but-recent sink should survive.
    s2 = _ts.create("run_recent_closed", thread_id="t", started_at="now")
    s2.push({"type": "x"})
    s2.close()    # closed_at = now → recent

    assert _ts.get("run_old_closed") is not None
    assert _ts.get("run_recent_closed") is not None

    result = _ts.sweep_once()
    assert result["sinks_evicted"] >= 1, result
    assert _ts.get("run_old_closed") is None       # evicted
    assert _ts.get("run_recent_closed") is not None  # kept
    # JSONLs both survive (they're young — only sinks-in-memory got evicted).
    assert _ts._jsonl_path("run_old_closed").exists()


def test_disk_last_seq_reads_highest_from_jsonl():
    """For active-turn lookups + restart resume hints."""
    _fresh()
    from core.runtime import turn_sink as _ts
    s = _ts.create("run_last_seq", thread_id="t", started_at="now")
    for _ in range(7):
        s.push({"type": "x"})
    s.close()
    _ts._REGISTRY.clear()    # simulate restart
    assert _ts.disk_last_seq("run_last_seq") == 7
    assert _ts.disk_last_seq("run_does_not_exist") == 0


def test_push_failures_dont_block_dispatch():
    """If disk writes fail for any reason, push() must still record
    in-memory + fan out to subscribers — durability is best-effort,
    not load-bearing for the live stream."""
    _fresh()
    from core.runtime import turn_sink as _ts
    s = _ts.create("run_disk_fail", thread_id="t", started_at="now")
    # Sabotage the file handle to force a write error.
    s._jsonl_fh = None
    # Make _jsonl_path return an unwritable target so open() fails.
    saved = _ts._jsonl_path
    def _bad_path(rid): return Path("/proc/self/this_will_fail.jsonl")
    _ts._jsonl_path = _bad_path
    try:
        seq = s.push({"type": "x"})
        assert seq == 1, "push must still increment seq + return"
        assert s.last_seq == 1
        # In-memory tail still has it
        assert len(s.replay_since(0)) == 1
    finally:
        _ts._jsonl_path = saved


def main() -> int:
    tests = [
        test_push_writes_jsonl_to_disk,
        test_rehydrate_reads_all_events_from_disk,
        test_rehydrate_honors_since,
        test_replay_since_falls_back_to_disk_when_tail_rolled,
        test_sweep_deletes_old_jsonls,
        test_sweep_evicts_stale_closed_sinks,
        test_disk_last_seq_reads_highest_from_jsonl,
        test_push_failures_dont_block_dispatch,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
