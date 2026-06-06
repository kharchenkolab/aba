"""Stage 4 tests: Run lifecycle — idle timeout + ambient promotion.

Covers:
  - close_idle_runs closes a Run whose updated_at is older than threshold
  - close_idle_runs leaves a fresh Run alone
  - close_idle_runs respects thread_id filter
  - close_idle_runs preserves children (a populated Run gets state=closed,
    not archived; an empty Run gets discarded via close_run's empty-Run path)
  - materialize_run_from_ambient promotes an ambient analysis: ambient flag
    removed, title updated, entity id unchanged
  - open_run opportunistically sweeps idle Runs from OTHER threads

Run:  .venv/bin/python tests/test_exec_records_stage4.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_execrec_s4_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "s4.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn          # noqa: E402
from core.graph import entities                         # noqa: E402
from content.bio.lifecycle import runs                  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _backdate_updated_at(entity_id: str, minutes_ago: int) -> None:
    """Force an entity's updated_at to N minutes in the past so the idle
    sweeper sees it as stale. update_entity always sets updated_at to now;
    we go around that here to test the sweeper without sleeping."""
    iso = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    with _conn() as c:
        c.execute("UPDATE entities SET updated_at = ? WHERE id = ?", (iso, entity_id))
        c.commit()


def _add_child(parent_id: str) -> str:
    """Give a Run a non-archived child so close_run keeps it instead of
    archiving as empty. We use a figure with a dummy artifact_path because
    the entity_types validator requires figures to have one."""
    return entities.create_entity(
        entity_type="figure", title="child fig",
        artifact_path="/tmp/dummy.png",
        parent_entity_id=parent_id,
    )


def test_close_idle_runs_closes_stale_populated_run():
    print("\n[1] close_idle_runs closes a stale populated Run")
    init_db()
    rid = runs.open_run("thr_s4a", "Test")
    _add_child(rid)
    # Backdate so it looks idle
    _backdate_updated_at(rid, minutes_ago=60)
    closed = runs.close_idle_runs(thread_id="thr_s4a", idle_seconds=1800)
    check("close_idle_runs returned one id", closed == [rid])
    rec = entities.get_entity(rid)
    check("Run row preserved (not archived)", rec is not None and rec.get("status") != "archived")
    md = rec.get("metadata") or {} if rec else {}
    check("Run metadata.run_state = closed", md.get("run_state") == "closed")
    # active_run_id should no longer see it
    check("active_run_id no longer finds it",
          runs.active_run_id("thr_s4a") is None)


def test_close_idle_runs_leaves_fresh_run_alone():
    print("\n[2] close_idle_runs leaves a fresh Run alone")
    rid = runs.open_run("thr_s4b", "Fresh")
    _add_child(rid)
    # Don't backdate — it's fresh
    closed = runs.close_idle_runs(thread_id="thr_s4b", idle_seconds=1800)
    check("no Runs closed", closed == [])
    check("Run is still active", runs.active_run_id("thr_s4b") == rid)


def test_close_idle_runs_thread_filter():
    print("\n[3] close_idle_runs respects thread_id filter")
    rid_a = runs.open_run("thr_s4c", "A"); _add_child(rid_a)
    rid_b = runs.open_run("thr_s4d", "B"); _add_child(rid_b)
    _backdate_updated_at(rid_a, minutes_ago=60)
    _backdate_updated_at(rid_b, minutes_ago=60)
    closed = runs.close_idle_runs(thread_id="thr_s4c", idle_seconds=1800)
    check("only thr_s4c run closed", closed == [rid_a])
    check("thr_s4d run still active",
          runs.active_run_id("thr_s4d") == rid_b)


def test_close_idle_runs_archives_empty_idle_run():
    print("\n[4] close_idle_runs discards empty stale Runs (no children, no code)")
    rid = runs.open_run("thr_s4e", "Empty")
    _backdate_updated_at(rid, minutes_ago=60)
    closed = runs.close_idle_runs(thread_id="thr_s4e", idle_seconds=1800)
    check("empty Run got closed via close_run path", closed == [rid])
    rec = entities.get_entity(rid)
    # close_run archives empty Runs (per existing behavior).
    check("empty stale Run is archived",
          rec is not None and rec.get("status") == "archived")


def test_no_close_below_threshold():
    print("\n[5] close_idle_runs with 60s threshold leaves 10s-old Runs alone")
    rid = runs.open_run("thr_s4f", "Recent")
    _add_child(rid)
    # 10 seconds ago — fresh under any reasonable threshold
    _backdate_updated_at(rid, minutes_ago=0)  # i.e. now
    closed = runs.close_idle_runs(thread_id="thr_s4f", idle_seconds=60)
    check("no Run closed (below threshold)", closed == [])


def test_materialize_run_from_ambient():
    print("\n[6] materialize_run_from_ambient promotes ambient analysis")
    # Set up: simulate an ambient analysis (what registry._ensure_analysis would create).
    aid = entities.create_entity(
        entity_type="analysis", title="Analysis",
        metadata={"thread_id": "thr_s4g", "run_state": "open",
                  "origin": "internal", "ambient": True},
    )
    # Sanity: ambient flag set
    rec_before = entities.get_entity(aid)
    md_before = (rec_before or {}).get("metadata") or {}
    check("ambient flag initially True", md_before.get("ambient") is True)

    promoted = runs.materialize_run_from_ambient("thr_s4g", title="DE on PBMC dataset")
    check("returned the existing analysis id", promoted == aid)
    rec_after = entities.get_entity(aid)
    md_after = (rec_after or {}).get("metadata") or {}
    check("ambient flag removed", "ambient" not in md_after)
    check("title updated",
          (rec_after or {}).get("title") == "DE on PBMC dataset")
    check("run_state preserved (still open)", md_after.get("run_state") == "open")


def test_materialize_run_idempotent():
    print("\n[7] materialize_run_from_ambient is idempotent on already-promoted Runs")
    aid = entities.create_entity(
        entity_type="analysis", title="Already Named",
        metadata={"thread_id": "thr_s4h", "run_state": "open",
                  "origin": "internal"},  # no ambient flag
    )
    promoted = runs.materialize_run_from_ambient("thr_s4h", title="New Title")
    check("returns id (idempotent)", promoted == aid)
    rec = entities.get_entity(aid)
    check("title updated", (rec or {}).get("title") == "New Title")
    check("ambient flag still absent",
          "ambient" not in ((rec or {}).get("metadata") or {}))


def test_materialize_run_no_active_run():
    print("\n[8] materialize_run_from_ambient returns None when no Run is active")
    # New thread, never opened a Run
    promoted = runs.materialize_run_from_ambient("thr_s4i", title="Anything")
    check("returns None", promoted is None)


def test_open_run_opportunistic_sweep():
    print("\n[9] open_run sweeps idle Runs in OTHER threads")
    rid_old = runs.open_run("thr_s4j_old", "Old")
    _add_child(rid_old)
    _backdate_updated_at(rid_old, minutes_ago=60)
    # Opening a Run in a DIFFERENT thread should sweep the old one.
    rid_new = runs.open_run("thr_s4k_new", "New")
    _add_child(rid_new)
    check("old thread's Run is no longer active",
          runs.active_run_id("thr_s4j_old") is None)
    check("new thread's Run is active", runs.active_run_id("thr_s4k_new") == rid_new)


def main() -> int:
    test_close_idle_runs_closes_stale_populated_run()
    test_close_idle_runs_leaves_fresh_run_alone()
    test_close_idle_runs_thread_filter()
    test_close_idle_runs_archives_empty_idle_run()
    test_no_close_below_threshold()
    test_materialize_run_from_ambient()
    test_materialize_run_idempotent()
    test_materialize_run_no_active_run()
    test_open_run_opportunistic_sweep()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS STAGE-4 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
