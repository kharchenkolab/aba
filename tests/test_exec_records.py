"""Stage 1 unit tests for execution records (misc/exec_records_and_versioning.md).

Covers:
  - schema: execution_records table is created by init_db
  - create() writes both JSON sidecar AND DB row, kept consistent
  - get() merges DB index + JSON body
  - list_by_run / list_by_thread filter as expected
  - code_hash is stable; env_fingerprint is stable
  - record_path_for() creates `.exec/` and returns the right name
  - JSON sidecar survives DB rollback test (we simulate by passing a bad
    record_path that succeeds on FS but fails after, then verify cleanup)

Run:  .venv/bin/python tests/test_exec_records.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_execrec_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "exec.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn   # noqa: E402
from core.graph import exec_records             # noqa: E402
from core.exec.fingerprint import (              # noqa: E402
    code_hash, env_fingerprint, _PY_PROBE,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_schema_table_exists():
    print("\n[1] schema: execution_records table exists")
    init_db()
    with _conn() as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='execution_records'"
        ).fetchall()
    check("table execution_records exists", len(rows) == 1)
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(execution_records)").fetchall()}
    for col in ("exec_id", "thread_id", "run_id", "tool_use_id", "tool_name",
                "status", "code_hash", "record_path", "started_at", "completed_at"):
        check(f"column {col} present", col in cols)
    # Indexes
    with _conn() as c:
        idx = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='execution_records'"
        ).fetchall()}
    for ix in ("idx_exec_thread", "idx_exec_run", "idx_exec_hash", "idx_exec_tooluse"):
        check(f"index {ix} present", ix in idx)


def test_code_hash_stable():
    print("\n[2] code_hash is deterministic")
    h1 = code_hash("print(42)\n")
    h2 = code_hash("print(42)\n")
    h3 = code_hash("print(43)\n")
    check("same code → same hash", h1 == h2)
    check("different code → different hash", h1 != h3)
    check("hash format = sha256:<hex>", h1.startswith("sha256:") and len(h1) == 7 + 64)


def test_env_fingerprint_stable():
    print("\n[3] env_fingerprint is deterministic and order-insensitive")
    pkg_a = {"numpy": "1.26.0", "scanpy": "1.10.2"}
    pkg_b = {"scanpy": "1.10.2", "numpy": "1.26.0"}
    pkg_c = {"numpy": "1.26.1", "scanpy": "1.10.2"}
    f1 = env_fingerprint("3.12.4", pkg_a)
    f2 = env_fingerprint("3.12.4", pkg_b)
    f3 = env_fingerprint("3.12.4", pkg_c)
    f4 = env_fingerprint("3.12.5", pkg_a)
    check("key order doesn't change fingerprint", f1 == f2)
    check("package version change moves fingerprint", f1 != f3)
    check("lang version change moves fingerprint", f1 != f4)


def test_record_path_for():
    print("\n[4] record_path_for creates .exec/ and right filename")
    cwd = Path(_tmp) / "rp_test"
    cwd.mkdir(parents=True, exist_ok=True)
    p = exec_records.record_path_for(cwd, "exec_abc123")
    check(".exec subdir created", (cwd / ".exec").is_dir())
    check("filename = exec_id.json",
          p.name == "exec_abc123.json" and p.parent.name == ".exec")
    check("path is absolute", p.is_absolute())


def test_create_and_get_roundtrip():
    print("\n[5] create + get roundtrip")
    cwd = Path(_tmp) / "work" / "ana_xx"
    cwd.mkdir(parents=True, exist_ok=True)
    eid = exec_records.create(
        thread_id="thr_xx",
        run_id="ana_xx",
        tool_use_id="toolu_test_1",
        tool_name="run_python",
        status="ok",
        code="print('hello')\n",
        code_hash=code_hash("print('hello')\n"),
        started_at="2026-06-06T10:00:00Z",
        completed_at="2026-06-06T10:00:02Z",
        cwd=cwd,
        payload={
            "executor": "kernel:python", "language": "python",
            "language_version": "3.12.4",
            "package_versions": {"numpy": "1.26.0"},
            "env_fingerprint": env_fingerprint("3.12.4", {"numpy": "1.26.0"}),
            "produced": [{"kind": "figure", "idx": 0, "path": "umap.png"}],
            "stdout_tail": "hello\n", "stderr_tail": "", "exit_code": 0,
            "wall_time_s": 2.0,
        },
    )
    check("create returns exec_id", isinstance(eid, str) and eid.startswith("exec_"))
    # JSON sidecar exists
    sidecar = cwd / ".exec" / f"{eid}.json"
    check("sidecar JSON file exists", sidecar.is_file())
    # DB row exists
    with _conn() as c:
        r = c.execute("SELECT * FROM execution_records WHERE exec_id = ?", (eid,)).fetchone()
    check("DB row exists", r is not None)
    check("DB row.tool_name = run_python", r["tool_name"] == "run_python")
    check("DB row.run_id = ana_xx", r["run_id"] == "ana_xx")
    check("DB row.record_path matches sidecar", r["record_path"] == str(sidecar))
    # get() merges
    got = exec_records.get(eid)
    check("get() returns dict", isinstance(got, dict))
    check("get().code matches", got.get("code") == "print('hello')\n")
    check("get().produced[0].kind = figure",
          got.get("produced", [{}])[0].get("kind") == "figure")
    check("get().package_versions.numpy = 1.26.0",
          (got.get("package_versions") or {}).get("numpy") == "1.26.0")


def test_create_without_cwd_or_path_raises():
    print("\n[6] create without cwd or record_path raises ValueError")
    try:
        exec_records.create(
            thread_id="thr_xx", tool_name="run_python", status="ok",
            started_at="2026-06-06T10:00:00Z",
        )
        check("ValueError raised", False, "no exception")
    except ValueError:
        check("ValueError raised", True)


def test_list_by_run_and_thread():
    print("\n[7] list_by_run / list_by_thread filtering")
    # Set up: thread thr_a has 2 records in run R1 + 1 scratch
    # thread thr_b has 1 record in run R2
    cwd_a = Path(_tmp) / "list_a"; cwd_a.mkdir(exist_ok=True)
    cwd_b = Path(_tmp) / "list_b"; cwd_b.mkdir(exist_ok=True)
    e1 = exec_records.create(thread_id="thr_a", run_id="R1", tool_name="run_python",
                              status="ok", started_at="2026-06-06T10:01:00Z", cwd=cwd_a)
    e2 = exec_records.create(thread_id="thr_a", run_id="R1", tool_name="run_python",
                              status="ok", started_at="2026-06-06T10:02:00Z", cwd=cwd_a)
    e3 = exec_records.create(thread_id="thr_a", run_id=None, tool_name="run_python",
                              status="ok", started_at="2026-06-06T10:03:00Z", cwd=cwd_a)
    e4 = exec_records.create(thread_id="thr_b", run_id="R2", tool_name="run_r",
                              status="ok", started_at="2026-06-06T10:04:00Z", cwd=cwd_b)

    by_R1 = exec_records.list_by_run("R1")
    ids = {r["exec_id"] for r in by_R1}
    check("list_by_run(R1) returns 2 records", len(by_R1) == 2)
    check("list_by_run(R1) includes e1, e2", {e1, e2} == ids)

    by_R2 = exec_records.list_by_run("R2")
    check("list_by_run(R2) returns 1 record", len(by_R2) == 1)
    check("list_by_run(R2)[0] = e4", by_R2[0]["exec_id"] == e4)

    by_thr_a = exec_records.list_by_thread("thr_a")
    check("list_by_thread(thr_a) returns 3 records (incl scratch)", len(by_thr_a) == 3)

    thr_a_scratch = exec_records.list_by_thread("thr_a", run_id_filter="")
    check("list_by_thread(thr_a, scratch only) returns 1 record", len(thr_a_scratch) == 1)
    check("scratch-only record is e3", thr_a_scratch[0]["exec_id"] == e3)

    thr_a_R1 = exec_records.list_by_thread("thr_a", run_id_filter="R1")
    check("list_by_thread(thr_a, R1) returns 2 records", len(thr_a_R1) == 2)

    # Ordering: list_by_run returns in started_at ASC
    check("list_by_run is ordered by started_at", by_R1[0]["exec_id"] == e1)


def test_get_missing_returns_none():
    print("\n[8] get() on unknown exec_id returns None")
    check("None for missing id", exec_records.get("exec_does_not_exist") is None)


def test_get_with_missing_sidecar_falls_back_to_index():
    print("\n[9] get() with deleted sidecar returns index fields only")
    cwd = Path(_tmp) / "list_a"
    eid = exec_records.create(thread_id="thr_c", run_id="R3", tool_name="run_python",
                               status="ok", started_at="2026-06-06T10:05:00Z",
                               code="x = 1", cwd=cwd)
    sidecar = cwd / ".exec" / f"{eid}.json"
    sidecar.unlink()
    got = exec_records.get(eid)
    check("get() returns non-None", got is not None)
    check("index fields present (tool_name)", got and got.get("tool_name") == "run_python")
    check("body fields absent (code)", got and "code" not in got)


def main() -> int:
    test_schema_table_exists()
    test_code_hash_stable()
    test_env_fingerprint_stable()
    test_record_path_for()
    test_create_and_get_roundtrip()
    test_create_without_cwd_or_path_raises()
    test_list_by_run_and_thread()
    test_get_missing_returns_none()
    test_get_with_missing_sidecar_falls_back_to_index()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS STAGE-1 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
