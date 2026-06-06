"""Cutover 1: bulk + aggregated-for-run helpers.

Covers:
  - lookup_codes_for_entities: bulk version of lookup_code_for_entity
      * empty input → {}
      * mix of exec-backed + legacy entities → both resolve correctly
      * dangling exec_id → falls back to legacy producing_code
      * entity with NO exec_id AND no producing_code → ""
      * single DB query for the exec_id batch (verify by spot-check)
  - aggregated_code_for_run: concatenated code by started_at
      * empty Run (no execs) → ""
      * multi-exec run → joined by separator, in started_at order
      * missing JSON sidecar → that exec is skipped, others succeed

Run: .venv/bin/python tests/test_exec_records_cutover1.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_co1_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "co1.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
from core.graph import entities, exec_records           # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_exec(run_id, thread_id, code, started_at):
    cwd = Path(_tmp) / f"work_{thread_id}"; cwd.mkdir(exist_ok=True)
    return exec_records.create(
        thread_id=thread_id, run_id=run_id, tool_name="run_python",
        status="ok", code=code, started_at=started_at,
        completed_at=started_at, cwd=cwd,
    )


def test_bulk_empty_input():
    print("\n[1] lookup_codes_for_entities([]) → {}")
    init_db()
    check("empty list returns empty dict",
          exec_records.lookup_codes_for_entities([]) == {})


def test_bulk_mixed():
    print("\n[2] mix of exec-backed + legacy entities")
    # Two exec-backed entities sharing the same exec (multi-figure run case)
    ex1 = _make_exec(None, "thr_co1a", "fig1_code\n", "2026-06-06T10:00:00Z")
    e1 = entities.create_entity(
        entity_type="figure", title="A1", artifact_path="/tmp/a1.png",
        exec_id=ex1, artifact_kind="figure", artifact_idx=0,
    )
    e2 = entities.create_entity(
        entity_type="figure", title="A2", artifact_path="/tmp/a2.png",
        exec_id=ex1, artifact_kind="figure", artifact_idx=1,
    )
    # Another exec-backed entity from a different exec
    ex2 = _make_exec(None, "thr_co1a", "fig3_code\n", "2026-06-06T10:01:00Z")
    e3 = entities.create_entity(
        entity_type="figure", title="A3", artifact_path="/tmp/a3.png",
        exec_id=ex2, artifact_kind="figure", artifact_idx=0,
    )
    # Legacy entity (producing_code only)
    e4 = entities.create_entity(
        entity_type="figure", title="A4 legacy", artifact_path="/tmp/a4.png",
        producing_code="legacy4\n",
    )
    # Empty entity (no code anywhere)
    e5 = entities.create_entity(
        entity_type="figure", title="A5 empty", artifact_path="/tmp/a5.png",
    )

    ents = [entities.get_entity(eid) for eid in (e1, e2, e3, e4, e5)]
    codes = exec_records.lookup_codes_for_entities(ents)
    check("e1 resolves to fig1_code", codes.get(e1) == "fig1_code\n")
    check("e2 (same exec as e1) also resolves to fig1_code",
          codes.get(e2) == "fig1_code\n")
    check("e3 resolves to fig3_code", codes.get(e3) == "fig3_code\n")
    check("e4 (legacy) resolves to legacy4", codes.get(e4) == "legacy4\n")
    check("e5 (empty) resolves to ''", codes.get(e5) == "")
    check("all 5 entities present in result",
          set(codes.keys()) == {e1, e2, e3, e4, e5})


def test_bulk_dangling_exec_id():
    print("\n[3] dangling exec_id → falls back to legacy producing_code")
    e = entities.create_entity(
        entity_type="figure", title="dangling", artifact_path="/tmp/d.png",
        producing_code="fallback after dangling\n",
        exec_id="exec_does_not_exist", artifact_kind="figure", artifact_idx=0,
    )
    rec = entities.get_entity(e)
    codes = exec_records.lookup_codes_for_entities([rec])
    check("resolves via legacy fallback when exec is dangling",
          codes.get(e) == "fallback after dangling\n")


def test_bulk_skips_none_and_no_id():
    print("\n[4] tolerates None entries and missing ids without raising")
    e1 = entities.create_entity(
        entity_type="figure", title="ok", artifact_path="/tmp/x.png",
        producing_code="ok\n",
    )
    rec = entities.get_entity(e1)
    codes = exec_records.lookup_codes_for_entities([None, {}, rec, {"foo": "bar"}])
    check("only the valid entity is in the result",
          set(codes.keys()) == {e1} and codes[e1] == "ok\n")


def test_aggregated_empty_run():
    print("\n[5] aggregated_code_for_run('') and (no execs) → ''")
    check("empty run_id → ''", exec_records.aggregated_code_for_run("") == "")
    check("nonexistent run_id → ''",
          exec_records.aggregated_code_for_run("R_does_not_exist") == "")


def test_aggregated_multi_exec_ordered():
    print("\n[6] aggregated_code_for_run concatenates in started_at order")
    rid = "R_aggregated_a"
    # Insert deliberately out of started_at order to verify ordering
    _make_exec(rid, "thr_co1b", "second\n", "2026-06-06T10:02:00Z")
    _make_exec(rid, "thr_co1b", "first\n",  "2026-06-06T10:01:00Z")
    _make_exec(rid, "thr_co1b", "third\n",  "2026-06-06T10:03:00Z")
    out = exec_records.aggregated_code_for_run(rid)
    check("output is non-empty", bool(out))
    parts = out.split("\n\n# ---\n")
    check("3 parts joined by # ---", len(parts) == 3, f"got {len(parts)}: {parts}")
    if len(parts) == 3:
        # Each part is the code with trailing newline preserved
        check("first part = 'first'", parts[0].strip() == "first")
        check("second part = 'second'", parts[1].strip() == "second")
        check("third part = 'third'", parts[2].strip() == "third")


def test_aggregated_skips_missing_sidecar():
    print("\n[7] aggregated_code_for_run skips an exec whose sidecar is gone")
    rid = "R_aggregated_b"
    ex1 = _make_exec(rid, "thr_co1c", "alpha\n", "2026-06-06T10:10:00Z")
    ex2 = _make_exec(rid, "thr_co1c", "beta\n",  "2026-06-06T10:11:00Z")
    # Delete ex1's sidecar to simulate hand-cleanup
    from core.graph._schema import _conn
    with _conn() as c:
        r = c.execute("SELECT record_path FROM execution_records WHERE exec_id = ?",
                      (ex1,)).fetchone()
    if r:
        Path(r["record_path"]).unlink(missing_ok=True)
    out = exec_records.aggregated_code_for_run(rid)
    check("only beta survives", out.strip() == "beta",
          f"got {out!r}")


def main() -> int:
    test_bulk_empty_input()
    test_bulk_mixed()
    test_bulk_dangling_exec_id()
    test_bulk_skips_none_and_no_id()
    test_aggregated_empty_run()
    test_aggregated_multi_exec_ordered()
    test_aggregated_skips_missing_sidecar()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS CUTOVER-1 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
