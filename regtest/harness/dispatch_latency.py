"""Standing screen: which tool calls WAITED rather than worked?

Read-only, no LLM, no agent — point it at any project DB after any session
(live, manual, or a regtest lane) and it turns a stall into a row with a cause.

WHY THIS EXISTS. Two production stalls were found by a person noticing a slow
session and reporting it, and neither could be diagnosed afterwards: a `Skill`
call (a registry read, 2 ms in every other turn of the same session) recorded
349 SECONDS, and a `run_python` recorded 134 s for 0.587 s of execution. Both
look identical to "the tool was slow", because `tool_invocations.duration_ms`
was queue-wait PLUS body time with no way to separate them. The recorder now
stamps `queue_wait_ms` and the contention it was dispatched into; this reads them
back so the finding is automatic instead of anecdotal.

  python regtest/harness/dispatch_latency.py [PROJECT_DB ...]
"""
from __future__ import annotations

import sqlite3
import sys

# A dispatch that waited longer than this did not "run slowly" — it queued.
STALL_MS = 5_000
# Above this share of its own duration, the wait IS the story.
WAIT_SHARE = 0.5


def _rows(db: str) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(tool_invocations)")}
        if "queue_wait_ms" not in cols:
            return [{"_blind": True}]          # pre-split DB: say so, never "clean"
        return [dict(r) for r in con.execute(
            "SELECT run_id, tool_name, duration_ms, queue_wait_ms, inflight, "
            "bg_backlog, started_at FROM tool_invocations "
            "WHERE queue_wait_ms IS NOT NULL ORDER BY queue_wait_ms DESC")]
    finally:
        con.close()


def audit(db: str) -> dict:
    """-> {measured, checked, stalls:[…], note}. `measured: False` means this DB
    predates the split and says NOTHING about queueing — the one answer that
    must never be reported as 'no stalls found'."""
    rows = _rows(db)
    if rows and rows[0].get("_blind"):
        return {"db": db, "measured": False, "checked": 0, "stalls": [],
                "note": "this DB has no queue_wait_ms column — dispatch latency "
                        "was never recorded here, so nothing is being screened"}
    if not rows:
        return {"db": db, "measured": False, "checked": 0, "stalls": [],
                "note": "no tool invocations carry a queue-wait stamp yet"}
    stalls = [r for r in rows
              if (r["queue_wait_ms"] or 0) >= STALL_MS
              and (r["queue_wait_ms"] or 0) >= WAIT_SHARE * max(1, r["duration_ms"] or 0)]
    return {"db": db, "measured": True, "checked": len(rows),
            "stalls": [{"tool": r["tool_name"], "run": r["run_id"],
                        "waited_ms": r["queue_wait_ms"],
                        "body_ms": (r["duration_ms"] or 0) - (r["queue_wait_ms"] or 0),
                        "inflight": r["inflight"],
                        "bg_backlog": r["bg_backlog"],
                        "at": r["started_at"]} for r in stalls],
            "worst_wait_ms": rows[0]["queue_wait_ms"]}


def checks(db: str) -> list[tuple[str, bool]]:
    """Lane-shaped verdicts. ARMED FIRST: an unmeasured DB fails as a
    precondition rather than answering the question it was asked."""
    a = audit(db)
    if not a["measured"]:
        return [(f"PRECONDITION: dispatch latency is recorded ({a['note']})", False)]
    return [(f"no tool call queued longer than {STALL_MS} ms "
             f"({a['checked']} checked, worst {a['worst_wait_ms']} ms)",
             not a["stalls"])]


def main(argv: list[str]) -> int:
    dbs = argv[1:]
    if not dbs:
        from pathlib import Path
        import os
        home = os.environ.get("ABA_RUNTIME_DIR") or os.path.expanduser("~/.aba/runtime")
        dbs = [str(p) for p in Path(home, "projects").glob("*/project.db")]
    rc = 0
    for db in dbs:
        a = audit(db)
        head = f"{a['db']}: "
        if not a["measured"]:
            print(head + "UNMEASURED — " + a["note"])
            rc = 1
            continue
        if not a["stalls"]:
            print(head + f"ok ({a['checked']} calls, worst wait "
                         f"{a['worst_wait_ms']} ms)")
            continue
        rc = 1
        print(head + f"{len(a['stalls'])} STALLED dispatch(es) of {a['checked']}:")
        for s in a["stalls"]:
            print(f"    {s['tool']:<22} waited {s['waited_ms']:>8} ms to do "
                  f"{s['body_ms']:>6} ms of work   "
                  f"({s['inflight']} dispatches in flight, "
                  f"{s['bg_backlog']} background queued)  {s['at']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
