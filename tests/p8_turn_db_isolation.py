"""Turn ↔ DB isolation: a background turn task must keep reading its OWN
project's database even when concurrent requests repoint the process-global
DB. This is the regression test for the 2026-06 cross-project corruption
incident.

ROOT CAUSE (the bug this guards against):
  core/graph/_schema.DB_PATH was a single process-global, mutated per-request
  by the _pin_project_per_request middleware (main.py). Agent turns, however,
  run as DETACHED asyncio tasks (core/runtime/turn_executor) that await on
  every LLM chunk / tool. When a SECOND open project was polled (the frontend
  hits /active-turn?project_id=<other> every ~1-2s), the middleware swapped
  the global DB out from under a running turn. The turn's next get_messages()
  read the WRONG project's rows — it lost the user's original instruction and
  produced a generic reply. No test caught it because the suite is
  single-project and never runs a streaming turn while a second project is
  active.

FIX:
  projects.bind(pid) binds the active project (+ its sqlite path) to the
  CURRENT execution context via contextvars; asyncio.create_task copies that
  context into the turn task. _conn() prefers the context-bound path over the
  global. turn_executor._drain wraps the whole turn in projects.bind(pid).

Deterministic. Isolated temp PROJECTS_DIR. Explicitly NOT single-project mode.

Run:
    .venv/bin/python tests/p8_turn_db_isolation.py
"""
from __future__ import annotations
import os
import sys
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Must NOT be single-project mode — that bypasses the multi-project layer that
# this test exercises. Point the runtime + projects dirs at a throwaway temp
# tree BEFORE importing core.config (which materializes dirs at import time).
os.environ.pop("ABA_DB_PATH", None)
os.environ.pop("ABA_DB_PATH", None)
_TMP = tempfile.mkdtemp(prefix="aba_turniso_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

from core import projects                                       # noqa: E402
from core.graph.messages import append_message, get_messages    # noqa: E402

projects.init()
A = projects.create_project("Alpha")["id"]
B = projects.create_project("Beta")["id"]

# Seed each project's default-workspace thread with a distinct instruction.
projects.set_current(A)
append_message("user", [{"type": "text", "text": "INSTRUCTION-A"}], thread_id="thr")
projects.set_current(B)
append_message("user", [{"type": "text", "text": "INSTRUCTION-B"}], thread_id="thr")


def _texts(msgs):
    return [b["text"] for m in msgs for b in m["content"] if b.get("type") == "text"]


def _read():
    return _texts(get_messages("workspace", thread_id="thr"))


def test_unprotected_global_read_is_corrupted_by_a_concurrent_switch():
    """CONTROL — proves the vulnerability is real and this harness reproduces
    it. A plain (unbound) read after another 'request' switched projects
    returns the WRONG project's rows. This is exactly what corrupted the turn
    before the fix; projects.bind() (next test) is what prevents it."""
    projects.set_current(A)
    assert _read() == ["INSTRUCTION-A"], _read()
    projects.set_current(B)               # a concurrent request for project B
    # Same logical read, now misrouted to B — A's instruction is gone:
    assert _read() == ["INSTRUCTION-B"], _read()


def test_bound_read_survives_a_concurrent_global_switch():
    """With projects.bind(A) active, a concurrent set_current(B) (the
    middleware pinning another project's poll) must NOT affect reads or
    current() inside the bound context."""
    with projects.bind(A):
        projects.set_current(B)           # concurrent 'other project' request
        assert projects.current() == A, projects.current()
        assert _read() == ["INSTRUCTION-A"], _read()
        projects.set_current(B)           # hammered again
        assert _read() == ["INSTRUCTION-A"], _read()
    # After the context exits, the global (now B) shows through again.
    assert projects.current() == B, projects.current()


def test_bind_nesting_restores_previous_context():
    projects.set_current(A)
    with projects.bind(A):
        with projects.bind(B):
            assert projects.current() == B and _read() == ["INSTRUCTION-B"]
        assert projects.current() == A and _read() == ["INSTRUCTION-A"]
    assert projects.current() == A


def test_concurrent_turn_tasks_each_see_their_own_project():
    """The real shape: two background turns (asyncio tasks) bound to A and B
    run concurrently while an 'attacker' coroutine flips the process-global
    every tick (like the frontend polling two open projects). Every read in
    each turn must see its OWN instruction — never the other's, never empty."""
    async def turn(pid, observed):
        with projects.bind(pid):
            for _ in range(40):
                await asyncio.sleep(0)     # yield: let the attacker repoint the global
                observed.append(_read())

    async def attacker():
        for _ in range(40):
            for p in (A, B):
                projects.set_current(p)
                await asyncio.sleep(0)

    async def run():
        obsA, obsB = [], []
        await asyncio.gather(turn(A, obsA), turn(B, obsB), attacker())
        assert obsA and all(r == ["INSTRUCTION-A"] for r in obsA), \
            f"A-turn saw foreign/empty reads: {sorted(map(tuple, obsA))}"
        assert obsB and all(r == ["INSTRUCTION-B"] for r in obsB), \
            f"B-turn saw foreign/empty reads: {sorted(map(tuple, obsB))}"

    asyncio.run(run())


def test_turn_executor_drain_binds_the_captured_project():
    """End-to-end through turn_executor: start_turn captures the project (A),
    spawns the detached _drain task; while it runs we flip the global to B
    repeatedly. The turn body's get_messages() must still read A."""
    from core.runtime import turn_executor as te

    seen = []

    async def fake_body():
        for _ in range(12):
            await asyncio.sleep(0)
            seen.append(_read())
            yield {"type": "delta", "text": "x"}

    async def run():
        projects.set_current(A)            # request pins A, then starts the turn
        rid = te.new_run_id()
        sink = te.start_turn(run_id=rid, thread_id="thr",
                             started_at="t0", body_gen=fake_body())
        for _ in range(12):                # concurrent requests for B mid-turn
            projects.set_current(B)
            await asyncio.sleep(0)
        await sink._task                   # let the turn drain to completion
        assert seen and all(r == ["INSTRUCTION-A"] for r in seen), \
            f"turn body read wrong project: {seen}"

    asyncio.run(run())


def main() -> int:
    tests = [
        test_unprotected_global_read_is_corrupted_by_a_concurrent_switch,
        test_bound_read_survives_a_concurrent_global_switch,
        test_bind_nesting_restores_previous_context,
        test_concurrent_turn_tasks_each_see_their_own_project,
        test_turn_executor_drain_binds_the_captured_project,
    ]
    failed = []
    for t in tests:
        # Each test re-establishes the global it needs; reset between for hygiene.
        projects.set_current(A)
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
