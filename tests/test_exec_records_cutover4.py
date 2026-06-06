"""Cutover 4 tests: producing_code column is dropped from entities.

Covers:
  - After init_db, entities table has NO producing_code column
  - create_entity rejects producing_code parameter (TypeError)
  - update_entity ignores producing_code if passed
  - _row_to_entity doesn't return a producing_code key
  - Upgrade scenario: add the column back via raw SQL (simulating an
    upgrade from a pre-cutover DB), populate it, call init_db again, and
    verify backfill ran + column got dropped + entities have exec_id
  - lookup_code_for_entity post-cutover: works via exec_id only, no
    legacy fallback path exists

Run: .venv/bin/python tests/test_exec_records_cutover4.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_co4_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "co4.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn          # noqa: E402
from core.graph import entities, exec_records          # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_column_gone_after_init():
    print("\n[1] entities table no longer has producing_code column")
    init_db()
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(entities)").fetchall()}
    check("producing_code NOT in columns", "producing_code" not in cols)
    # exec_id IS in columns (Stage 2 added it)
    check("exec_id in columns", "exec_id" in cols)


def test_create_entity_rejects_producing_code():
    print("\n[2] create_entity rejects producing_code kwarg (TypeError)")
    try:
        entities.create_entity(
            entity_type="figure", title="should fail",
            artifact_path="/tmp/x.png",
            producing_code="should not be accepted",
        )
        check("TypeError raised", False, "no exception")
    except TypeError as e:
        msg = str(e)
        check("TypeError raised with 'producing_code' in message",
              "producing_code" in msg, f"got {msg!r}")


def test_row_to_entity_has_no_producing_code():
    print("\n[3] _row_to_entity dict has no producing_code key")
    eid = entities.create_entity(
        entity_type="figure", title="no key", artifact_path="/tmp/n.png",
    )
    rec = entities.get_entity(eid)
    check("'producing_code' NOT in returned dict",
          rec is not None and "producing_code" not in rec)


def test_update_entity_ignores_producing_code():
    print("\n[4] update_entity ignores producing_code (silent drop)")
    # update_entity's allowed_set no longer includes producing_code, so
    # passing it is silently ignored (no SQL touches that column).
    eid = entities.create_entity(
        entity_type="figure", title="upd", artifact_path="/tmp/u.png",
    )
    out = entities.update_entity(eid, producing_code="ignored")
    check("update_entity returned the row", out is not None)
    check("no producing_code surfaced",
          out is not None and "producing_code" not in out)


def test_lookup_only_via_exec_id():
    print("\n[5] lookup_code_for_entity only resolves via exec_id")
    # Entity with exec_id → resolves
    cwd = Path(_tmp) / "lookup"; cwd.mkdir(exist_ok=True)
    ex = exec_records.create(
        thread_id="thr_co4", run_id=None, tool_name="run_python",
        status="ok", code="found via exec\n",
        started_at="2026-06-06T11:00:00Z", cwd=cwd,
    )
    e1 = entities.create_entity(
        entity_type="figure", title="With exec", artifact_path="/tmp/we.png",
        exec_id=ex, artifact_kind="figure", artifact_idx=0,
    )
    rec1 = entities.get_entity(e1)
    check("entity with exec_id resolves to its code",
          exec_records.lookup_code_for_entity(rec1) == "found via exec\n")
    # Entity without exec_id → ""
    e2 = entities.create_entity(
        entity_type="figure", title="No exec", artifact_path="/tmp/ne.png",
    )
    rec2 = entities.get_entity(e2)
    check("entity without exec_id resolves to ''",
          exec_records.lookup_code_for_entity(rec2) == "")


def test_upgrade_scenario():
    print("\n[6] upgrade scenario: column gets re-dropped + entities backfilled")
    # Simulate a pre-cutover DB: re-add the column, write a legacy entity,
    # then call init_db AGAIN. The migration path should backfill the
    # entity and re-drop the column.
    with _conn() as c:
        c.execute("ALTER TABLE entities ADD COLUMN producing_code TEXT")
        c.commit()
    # Verify the column was added
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(entities)").fetchall()}
    check("column re-added for simulation", "producing_code" in cols)
    # Create an entity and write producing_code via raw SQL
    eid = entities.create_entity(
        entity_type="figure", title="Upgrade me", artifact_path="/tmp/up.png",
    )
    with _conn() as c:
        c.execute("UPDATE entities SET producing_code = ? WHERE id = ?",
                  ("upgrade-path-code\n", eid))
        c.commit()
    # Sanity: column has the value
    with _conn() as c:
        r = c.execute("SELECT producing_code, exec_id FROM entities WHERE id = ?",
                      (eid,)).fetchone()
    check("legacy code present pre-upgrade",
          r and r["producing_code"] == "upgrade-path-code\n")
    check("exec_id is None pre-upgrade",
          r and r["exec_id"] is None)

    # Re-run init_db → migration kicks in: backfill, then column drop
    init_db()

    # Column is gone again
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(entities)").fetchall()}
    check("column dropped after re-init", "producing_code" not in cols)
    # Entity now has exec_id set
    rec = entities.get_entity(eid)
    check("entity has exec_id post-upgrade", bool(rec.get("exec_id")))
    # And lookup_code_for_entity returns the original code via the
    # synthetic exec record
    looked = exec_records.lookup_code_for_entity(rec)
    check("lookup returns the legacy code through the backfilled exec",
          looked == "upgrade-path-code\n", f"got {looked!r}")


def main() -> int:
    test_column_gone_after_init()
    test_create_entity_rejects_producing_code()
    test_row_to_entity_has_no_producing_code()
    test_update_entity_ignores_producing_code()
    test_lookup_only_via_exec_id()
    test_upgrade_scenario()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS CUTOVER-4 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
