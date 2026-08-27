"""Background work must not queue against the user's next tool call.

Every tool dispatch goes through `projects.in_thread` → asyncio's DEFAULT
executor, min(32, cpu+4) slots, process-wide. `projects.spawn` used the SAME
executor for advisor reviews, proposal evaluation and skeptic/stylist passes —
all of which make LLM calls and hold a slot for tens of seconds to minutes.

A saturated default executor makes EVERY dispatch wait, and the tool's recorded
`duration_ms` charges that wait to the tool. Two production stalls have exactly
that shape: a `Skill` call (a registry read, 2 ms in every other turn of the
same session) recorded 349 SECONDS, and a `run_python` recorded 134 s for
0.587 s of actual execution. Neither was diagnosable, because the row could not
say whether the tool was slow or merely queued.

So: background work gets its own bounded pool, and the telemetry splits the
clock.

Run: python backend/tests/test_background_never_starves_dispatch.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_bgpool_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "b.db"))
_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core import projects  # noqa: E402

DISPATCH_BUDGET_S = 2.0


def test_saturated_background_work_does_not_delay_a_tool_dispatch():
    """THE guard. Fill the background pool past its cap with blocking work —
    the shape of several advisor reviews in flight — then dispatch a tool.

    Asserts the forbidden ACTION (the dispatch waiting), not just that it
    eventually returned: a dispatch that completes after the blockers are
    released would satisfy a naive check while being the exact bug."""
    pool = projects.background_pool()
    over = pool._max_workers * 2          # twice the cap: a real backlog
    hold = threading.Event()

    async def main():
        for _ in range(over):
            projects.spawn(hold.wait)
        await asyncio.sleep(0.5)          # let them occupy every slot
        t0 = time.time()
        try:
            # BOUND the wait. With spawn back on the dispatch executor this
            # never returns AT ALL, and a guard that hangs is not a guard —
            # CI reports a timeout, which reads as flakiness, not a diagnosis.
            await asyncio.wait_for(projects.in_thread(lambda: 1 + 1),
                                   timeout=DISPATCH_BUDGET_S)
        except asyncio.TimeoutError:
            return float("inf")
        finally:
            # Release INSIDE the loop. asyncio.run()'s teardown joins the
            # default executor, so if the blockers are sitting on it (the very
            # bug this guards) the join never completes and the guard hangs
            # instead of failing. Draining first makes the failure legible.
            hold.set()
        return time.time() - t0

    try:
        waited = asyncio.run(main())
    finally:
        hold.set()
    assert waited < DISPATCH_BUDGET_S, (
        f"a trivial tool dispatch waited {waited:.1f}s behind {over} background "
        f"tasks (inf = never dispatched at all) — background work is "
        f"sharing the dispatch executor again")


def test_background_work_is_bounded_and_separate():
    """ARMED the other way: the pool must be a REAL, bounded, distinct
    executor. A 'fix' that handed spawn the default executor back, or gave it
    an unbounded one, passes the timing test above on a fast box."""
    pool = projects.background_pool()
    assert pool is projects.background_pool(), "the pool must be a singleton"
    assert 0 < pool._max_workers <= 32, "background work must stay bounded"

    seen: dict = {}

    async def main():
        await projects.in_thread(
            lambda: seen.__setitem__("dispatch", threading.current_thread().name))
        fut = projects.spawn(
            lambda: seen.__setitem__("background", threading.current_thread().name))
        await asyncio.wrap_future(fut) if not hasattr(fut, "__await__") else await fut

    asyncio.run(main())
    assert seen["background"].startswith("aba-bg"), seen
    assert not seen["dispatch"].startswith("aba-bg"), (
        f"tool dispatch ran on the BACKGROUND pool ({seen['dispatch']}) — the "
        f"two must not share, in either direction")


def test_spawn_still_runs_inline_without_a_loop():
    """WIDE — the degenerate caller: a sync FastAPI route has no running loop,
    and spawn must run inline there rather than losing the work."""
    ran: list = []
    assert projects.spawn(lambda: ran.append(1)) is None
    assert ran == [1]


def test_queue_wait_is_recorded_apart_from_body_time():
    """The blindness that made both stalls undiagnosable: `duration_ms` is
    queue-wait PLUS body time, and the row could not tell them apart.

    Behavioural, not a source grep — an earlier version of this guard looked
    for a variable name in guide.py and stayed green when the wrapper was
    wired out."""
    from core.runtime.tool_telemetry import timed_body
    sink: list = []
    wrapped = timed_body(lambda x: x * 2, sink)
    dispatched = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc)
    time.sleep(0.25)                      # the queue wait
    assert wrapped(21) == 42
    assert len(sink) == 1, "the body's start instant must be stamped exactly once"
    queue_wait = (sink[0] - dispatched).total_seconds()
    assert 0.2 < queue_wait < 5.0, queue_wait

    # and it stamps even when the body fails — an errored tool that WAITED is
    # the most interesting row of all
    sink2: list = []
    try:
        timed_body(lambda: (_ for _ in ()).throw(ValueError("boom")), sink2)()
    except ValueError:
        pass
    assert len(sink2) == 1


def test_the_dispatch_site_actually_uses_the_wrapper():
    """Wiring check, anchored on the load-bearing token: the callable handed to
    in_thread must be the WRAPPED one. Greps for the argument, not for the
    presence of a variable somewhere nearby."""
    src = Path(_BACKEND, "guide.py").read_text()
    i = src.index("_projects_mod.in_thread(")
    assert "_exec_tool_timed" in src[i:i + 120], (
        "guide.py dispatches the bare tool body — queue wait is folded back "
        "into duration_ms and a stall becomes undiagnosable again")


def test_the_telemetry_row_can_hold_the_split(tmp_path):
    """Schema half: the columns exist and round-trip. A recorder that computes
    queue wait and drops it on the floor is the same blindness."""
    import sqlite3
    from core.graph import _schema
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.executescript("CREATE TABLE tool_invocations (id INTEGER PRIMARY KEY "
                      "AUTOINCREMENT, run_id TEXT, agent_spec TEXT, tool_name "
                      "TEXT, source TEXT, status TEXT, input_summary TEXT, "
                      "duration_ms INTEGER, error_summary TEXT, started_at "
                      "TEXT, ended_at TEXT)")
    for ddl in ("ALTER TABLE tool_invocations ADD COLUMN queue_wait_ms INTEGER",
                "ALTER TABLE tool_invocations ADD COLUMN pool_queued INTEGER",
                "ALTER TABLE tool_invocations ADD COLUMN pool_workers INTEGER"):
        con.execute(ddl)
    con.execute("INSERT INTO tool_invocations (tool_name, duration_ms, "
                "queue_wait_ms, pool_queued, pool_workers) VALUES (?,?,?,?,?)",
                ("Skill", 349_000, 348_998, 41, 32))
    row = con.execute("SELECT duration_ms, queue_wait_ms, pool_queued FROM "
                      "tool_invocations").fetchone()
    con.close()
    # the shape the live incident would have had, had it been measurable
    assert row == (349_000, 348_998, 41)
    assert row[0] - row[1] == 2, "body time is duration minus queue wait"
    assert "queue_wait_ms" in Path(_schema.__file__).read_text(), \
        "the live schema never gains the column, so production rows stay blind"


def _standalone() -> int:
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in (test_saturated_background_work_does_not_delay_a_tool_dispatch,
              test_background_work_is_bounded_and_separate,
              test_spawn_still_runs_inline_without_a_loop,
              test_queue_wait_is_recorded_apart_from_body_time,
              test_the_dispatch_site_actually_uses_the_wrapper):
        mp = _MP()
        try:
            t(mp) if "monkeypatch" in t.__code__.co_varnames else t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
