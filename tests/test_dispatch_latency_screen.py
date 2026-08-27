"""The standing screen that makes a dispatch stall self-reporting.

Two production stalls were found by a person noticing a slow session, and
neither survived as evidence. This guards the screen that replaces that
(regtest/harness/dispatch_latency.py).

Run: python tests/test_dispatch_latency_screen.py   (or via pytest)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regtest.harness.dispatch_latency import audit, checks  # noqa: E402

_COLS = ("run_id TEXT, tool_name TEXT, duration_ms INTEGER, started_at TEXT")


def _db(tmp_path, rows, *, with_split=True):
    p = tmp_path / "project.db"
    con = sqlite3.connect(p)
    extra = (", queue_wait_ms INTEGER, inflight INTEGER, bg_backlog INTEGER"
             if with_split else "")
    con.execute(f"CREATE TABLE tool_invocations (id INTEGER PRIMARY KEY, {_COLS}{extra})")
    for r in rows:
        if with_split:
            con.execute("INSERT INTO tool_invocations (run_id, tool_name, duration_ms,"
                        " started_at, queue_wait_ms, inflight, bg_backlog)"
                        " VALUES (?,?,?,?,?,?,?)", r)
        else:
            con.execute("INSERT INTO tool_invocations (run_id, tool_name, duration_ms,"
                        " started_at) VALUES (?,?,?,?)", r[:4])
    con.commit(); con.close()
    return str(p)


def test_it_names_the_stall_and_says_it_was_a_wait(tmp_path):
    """THE live shape: a 2 ms registry read recorded at 349 s. The screen must
    say the work was 2 ms and the wait was the rest."""
    db = _db(tmp_path, [
        ("run_1", "Skill", 349_000, "2026-08-26T01:00:00Z", 348_998, 41, 32),
        ("run_1", "run_python", 1_200, "2026-08-26T01:10:00Z", 4, 0, 32),
    ])
    a = audit(db)
    assert a["measured"] is True and a["checked"] == 2
    (s,) = a["stalls"]
    assert s["tool"] == "Skill" and s["body_ms"] == 2
    assert s["waited_ms"] == 348_998 and s["inflight"] == 41
    assert not all(ok for _n, ok in checks(db))


def test_a_genuinely_slow_tool_is_not_a_stall(tmp_path):
    """ARMED the other way: a tool that WORKED for two minutes is not queueing,
    and flagging it would make the screen noise nobody reads."""
    db = _db(tmp_path, [
        # worked the whole time
        ("run_1", "ensure_capability", 133_861, "2026-08-26T01:00:00Z", 12, 1, 32),
        # WIDE — the case that makes the share test load-bearing: a real
        # 6-second queue on a two-minute install is scheduling, not a stall.
        # Without the share rule this row is flagged and the screen becomes
        # noise on every busy session.
        ("run_1", "ensure_capability", 120_000, "2026-08-26T01:05:00Z", 6_000, 2, 32)])
    a = audit(db)
    assert a["measured"] is True and a["stalls"] == [], a["stalls"]
    assert all(ok for _n, ok in checks(db))


def test_a_db_that_never_recorded_the_split_reports_UNMEASURED(tmp_path):
    """WIDE — the degenerate DB, and the dangerous one: a pre-split project has
    no queue_wait_ms at all. 'No stalls found' there is a lie about a database
    that was never screened."""
    db = _db(tmp_path, [("run_1", "Skill", 349_000, "t")], with_split=False)
    a = audit(db)
    assert a["measured"] is False and a["stalls"] == []
    c = checks(db)
    assert len(c) == 1 and c[0][1] is False and "PRECONDITION" in c[0][0]


def test_an_empty_but_capable_db_is_also_unmeasured(tmp_path):
    """A project where no tool ever ran says nothing either — a green screen
    over zero observations is the failure mode this estate keeps hitting."""
    db = _db(tmp_path, [])
    assert audit(db)["measured"] is False
    assert not all(ok for _n, ok in checks(db))


def test_a_short_wait_under_the_floor_is_ignored(tmp_path):
    """The threshold has to hold at its own boundary: 4.9 s of wait on a 5 s
    call is normal scheduling, not a stall."""
    db = _db(tmp_path, [
        ("r", "run_python", 5_000, "t", 4_900, 3, 32)])
    assert audit(db)["stalls"] == []


def _standalone() -> int:
    import tempfile, traceback
    rc = 0
    for t in (test_it_names_the_stall_and_says_it_was_a_wait,
              test_a_genuinely_slow_tool_is_not_a_stall,
              test_a_db_that_never_recorded_the_split_reports_UNMEASURED,
              test_an_empty_but_capable_db_is_also_unmeasured,
              test_a_short_wait_under_the_floor_is_ignored,
              test_a_partial_schema_still_screens_instead_of_crashing):
        try:
            t(Path(tempfile.mkdtemp()))
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())


def test_a_partial_schema_still_screens_instead_of_crashing(tmp_path):
    """WIDE — the schema that shipped in between. `queue_wait_ms` landed before
    the contention columns were renamed to inflight/bg_backlog, so a DB written
    between the two commits has the first and not the others. Selecting them
    unguarded raised OperationalError and took the whole screen down: it then
    reported on NOTHING, which is worse than reporting partially."""
    import sqlite3
    p = tmp_path / "project.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE tool_invocations (id INTEGER PRIMARY KEY, run_id TEXT,"
                " tool_name TEXT, duration_ms INTEGER, started_at TEXT,"
                " queue_wait_ms INTEGER)")          # no inflight / bg_backlog
    con.execute("INSERT INTO tool_invocations (run_id, tool_name, duration_ms,"
                " started_at, queue_wait_ms) VALUES (?,?,?,?,?)",
                ("r", "Skill", 349_000, "t", 348_998))
    con.commit(); con.close()
    a = audit(str(p))
    assert a["measured"] is True and a["checked"] == 1
    (s,) = a["stalls"]
    assert s["waited_ms"] == 348_998 and s["body_ms"] == 2
    assert s["inflight"] is None and s["bg_backlog"] is None   # unknown, not crash
