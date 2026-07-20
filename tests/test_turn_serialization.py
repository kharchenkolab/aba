"""F8 per-thread turn serialization (queue-with-notice) — deterministic, no LLM.

Guards the turn_executor policy:
  1. DEDUP — the same text re-submitted while a thread's turn is still live
     (client double-POST from a second tab) returns the ORIGINAL sink; the
     duplicate generator never runs → one message never becomes two turns.
  2. QUEUE — a DIFFERENT text for a thread with a live turn waits for it: the
     new sink's FIRST event is a `notice`, and its real body does not start
     until the prior turn finishes → the two never interleave.
  3. RESUME — a continuation (no dedup_text) never queues (it would deadlock on
     the turn it continues); it runs immediately.

Run: python tests/test_turn_serialization.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_f8_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
os.environ.pop("ABA_DB_PATH", None)
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core import projects  # noqa: E402
projects.init()
projects.set_current(projects.create_project("F8")["id"])

from core.runtime import turn_executor as te  # noqa: E402
from core.runtime import turn_sink as ts      # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _events(sink):
    return [p for _seq, p in sink.replay_since(0)]


async def _run():
    TID = "threadA"
    release1 = asyncio.Event()
    body1_started = asyncio.Event()
    body3_started = {"v": False}

    async def gen1():
        body1_started.set()
        yield {"type": "delta", "text": "turn-1 working"}
        await release1.wait()            # hold turn 1 "live"
        yield {"type": "done"}

    async def gen_unused():
        # must NEVER run (dedup should collapse it)
        yield {"type": "delta", "text": "DUPLICATE SHOULD NOT RUN"}

    async def gen3():
        body3_started["v"] = True
        yield {"type": "delta", "text": "turn-3 working"}
        yield {"type": "done"}

    # ── turn 1 (new user turn) ──
    r1 = te.new_run_id()
    sink1 = te.start_turn(run_id=r1, thread_id=TID, started_at="t0",
                          body_gen=gen1(), dedup_text="hello")
    await body1_started.wait()
    check("turn-1 is the thread's live turn",
          te._thread_has_live_turn(TID) is not None)

    # ── (1) DEDUP: same text, still live → original sink, duplicate not run ──
    r2 = te.new_run_id()
    sink2 = te.start_turn(run_id=r2, thread_id=TID, started_at="t1",
                          body_gen=gen_unused(), dedup_text="hello")
    check("dedup returns the ORIGINAL sink (no second turn)",
          sink2.run_id == r1, f"got {sink2.run_id} vs {r1}")
    check("duplicate run_id was never registered",
          ts.get(r2) is None)

    # ── (2) QUEUE: different text, still live → notice first, body waits ──
    r3 = te.new_run_id()
    sink3 = te.start_turn(run_id=r3, thread_id=TID, started_at="t2",
                          body_gen=gen3(), dedup_text="different")
    check("queued turn got its OWN sink", sink3.run_id == r3)
    await asyncio.sleep(0.05)            # let the queued task emit its notice
    evs3 = _events(sink3)
    check("queued turn's FIRST event is a notice",
          bool(evs3) and evs3[0].get("type") == "notice",
          f"first={evs3[:1]}")
    check("queued turn's real body has NOT started (waiting on prior)",
          body3_started["v"] is False)

    # ── release turn 1; turn 3 should then run ──
    release1.set()
    for _ in range(100):
        await asyncio.sleep(0.02)
        if body3_started["v"]:
            break
    check("queued turn runs AFTER the prior finished", body3_started["v"] is True)
    await asyncio.sleep(0.05)
    evs3 = _events(sink3)
    check("queued turn's body ran after the notice (no interleave)",
          any(e.get("type") == "delta" and "turn-3" in (e.get("text") or "")
              for e in evs3))

    # ── (3) RESUME (dedup_text=None) never queues ──
    release4 = asyncio.Event()
    r4_started = asyncio.Event()

    async def gen4():
        r4_started.set()
        yield {"type": "delta", "text": "turn-4 live"}
        await release4.wait()
        yield {"type": "done"}

    r4 = te.new_run_id()
    te.start_turn(run_id=r4, thread_id=TID, started_at="t3",
                  body_gen=gen4(), dedup_text="turn4")
    await r4_started.wait()              # turn 4 now live
    resume_started = {"v": False}

    async def gen_resume():
        resume_started["v"] = True       # must start IMMEDIATELY, not queue
        yield {"type": "delta", "text": "resume"}
        yield {"type": "done"}

    r5 = te.new_run_id()
    sink5 = te.start_turn(run_id=r5, thread_id=TID, started_at="t4",
                          body_gen=gen_resume())      # dedup_text=None → resume
    await asyncio.sleep(0.05)
    check("resume did NOT queue behind the live turn (ran immediately)",
          resume_started["v"] is True)
    evs5 = _events(sink5)
    check("resume emitted NO queue notice",
          not any(e.get("type") == "notice" for e in evs5))
    release4.set()
    await asyncio.sleep(0.05)


def main():
    asyncio.run(_run())
    print(f"\n{'ALL PASS' if not _failures else f'FAILED ({len(_failures)})'}")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
