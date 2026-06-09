"""#14 — the Turn reaper must never fail/interrupt a turn that is RUNNING in
this process, and reap must run only once per project per process.

Background: reap_stale_turns() fails turns left in generating/executing_tools
(a dead previous process) and synthesizes 'interrupted' tool_results for their
orphaned tool_use. It was called from projects.set_current() on every project
switch — and the per-request middleware toggles projects constantly — so a
switch reaching a project mid-turn would fail its OWN live turn and write a
bogus 'interrupted' result that then collides with the real result.

Fixes under test:
  1. Liveness guard: reap skips run_ids / threads with a live turn task
     (turn_sink.live_run_ids / live_thread_ids).
  2. First-open memo: set_current() reaps a project only the first time this
     process opens it.

Deterministic. Isolated temp runtime. NOT single-project mode.

Run:
    .venv/bin/python tests/p11_reaper_liveness.py
"""
from __future__ import annotations
import os
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("ABA_DB_PATH", None)
os.environ.pop("ABA_DB_PATH_OVERRIDE", None)
_TMP = tempfile.mkdtemp(prefix="aba_p14_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                        # noqa: E402
from core.graph._schema import _conn                             # noqa: E402
from core.graph.messages import append_message, get_messages     # noqa: E402
from core.runtime import turn_sink                               # noqa: E402
import core.runtime.checkpoint as ckpt                           # noqa: E402

projects.init()


class _FakeRunningTask:
    """Stands in for a live asyncio turn task without needing a loop."""
    def done(self):
        return False


def _seed_run(run_id, thread_id, state="executing_tools"):
    with _conn() as c:
        c.execute(
            "INSERT INTO runs (run_id, session_id, turn_index, agent_spec_name, "
            "state, thread_id) VALUES (?,?,?,?,?,?)",
            (run_id, "sess", 0, "guide", state, thread_id),
        )
        c.commit()


def _seed_inflight_tool(thread_id, tu_id):
    append_message("assistant",
                   [{"type": "tool_use", "id": tu_id, "name": "run_python", "input": {}}],
                   entity_id="workspace", thread_id=thread_id)


def _run_state(run_id):
    with _conn() as c:
        r = c.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return r["state"] if r else None


def _has_interrupted_fill(thread_id):
    for m in get_messages("workspace", thread_id=thread_id):
        if m["role"] != "user":
            continue
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                try:
                    if isinstance(c, str) and json.loads(c).get("status") == "interrupted":
                        return True
                except Exception:
                    pass
    return False


def test_reap_skips_live_turn_but_reaps_dead_one():
    projects.create_project("reaper-live")    # own DB; create_project reaps once (empty)
    # A LIVE turn: registered sink with a running task + an in-flight tool.
    sink = turn_sink.create("run_live", "thr_live", "t0")
    sink._task = _FakeRunningTask()
    _seed_run("run_live", "thr_live")
    _seed_inflight_tool("thr_live", "tu_live")
    # A DEAD turn: same shape, but NO live task (owning process is gone).
    _seed_run("run_dead", "thr_dead")
    _seed_inflight_tool("thr_dead", "tu_dead")

    ckpt.reap_stale_turns()

    assert _run_state("run_live") == "executing_tools", \
        f"live turn was wrongly reaped: state={_run_state('run_live')}"
    assert _run_state("run_dead") == "failed", \
        f"dead turn should be reaped: state={_run_state('run_dead')}"
    assert not _has_interrupted_fill("thr_live"), \
        "synthesized an 'interrupted' fill for a LIVE in-flight tool"
    assert _has_interrupted_fill("thr_dead"), \
        "dead orphan should get a synthetic interrupted fill"
    turn_sink.evict("run_live")


def test_reap_is_memoized_to_first_open():
    pid = projects.create_project("reaper-memo")["id"]
    calls = {"n": 0}
    orig = ckpt.reap_stale_turns
    ckpt.reap_stale_turns = lambda: (calls.__setitem__("n", calls["n"] + 1), 0)[1]
    try:
        projects._reaped_pids.discard(pid)      # pretend this is the first open
        projects.set_current(pid)
        projects.set_current(pid)
        projects.set_current(pid)
        assert calls["n"] == 1, f"reap should run once per process per project, got {calls['n']}"
    finally:
        ckpt.reap_stale_turns = orig


def main() -> int:
    failed = []
    for t in [test_reap_skips_live_turn_but_reaps_dead_one,
              test_reap_is_memoized_to_first_open]:
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
        print(f"\n{len(failed)} failed")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
