"""Stage 1 integration test: run_python writes an execution_records row.

Spins up a REAL Jupyter Python kernel through the backend code path
(matches test_kernel_thread_env.py setup), calls run_python with a
simple code snippet, and verifies:

  - the result dict carries an `exec_id`
  - a row exists in execution_records with the right tool_use_id / thread_id
  - the JSON sidecar exists in <cwd>/.exec/<exec_id>.json
  - the sidecar has code, code_hash, language, env_fingerprint,
    package_versions, stdout_tail, exit_code

Isolated DB + runtime; uses the live envs overlay (read-only) so the
kernel's torch/scanpy imports work like in real session.

Run:  .venv/bin/python tests/test_exec_records_runpython.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_execrec_int_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "exec.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
# Live overlay so the python kernel can import normal libs.
os.environ["ABA_ENVS_DIR"] = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn   # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    from content.bio.tools.run_exec import run_python
    from core.graph import exec_records

    thread_id = "thr_inttest"
    tool_use_id = "toolu_inttest_1"
    ctx = {"thread_id": thread_id, "tool_use_id": tool_use_id}
    code = "x = 1 + 2\nprint('answer is', x)\n"

    print("\n[A] run_python with a trivial cell, verify exec_id returned")
    res = run_python({"code": code}, ctx=ctx)
    check("returncode == 0", res.get("returncode") == 0,
          f"got {res.get('returncode')!r}, stderr={res.get('stderr')!r}")
    check("exec_id present in result", isinstance(res.get("exec_id"), str)
          and res["exec_id"].startswith("exec_"),
          f"got {res.get('exec_id')!r}")
    eid = res.get("exec_id")
    if not eid:
        print("FAILED: no exec_id, cannot continue further checks")
        return 1

    print("\n[B] DB row in execution_records is populated")
    with _conn() as c:
        r = c.execute("SELECT * FROM execution_records WHERE exec_id = ?", (eid,)).fetchone()
    check("DB row exists", r is not None)
    if r is None:
        return 1
    check("thread_id matches", r["thread_id"] == thread_id)
    check("tool_use_id matches", r["tool_use_id"] == tool_use_id)
    check("tool_name = run_python", r["tool_name"] == "run_python")
    check("status = ok", r["status"] == "ok")
    check("code_hash is set", bool(r["code_hash"]) and r["code_hash"].startswith("sha256:"))
    check("record_path is absolute", r["record_path"] and r["record_path"].startswith("/"))
    check("record_path .exec subdir", "/.exec/" in r["record_path"])

    print("\n[C] JSON sidecar is well-formed and has the expected fields")
    sidecar = Path(r["record_path"])
    check("sidecar exists", sidecar.is_file())
    if not sidecar.is_file():
        return 1
    body = json.loads(sidecar.read_text(encoding="utf-8"))
    check("body.exec_id matches", body.get("exec_id") == eid)
    check("body.code matches input", body.get("code") == code)
    check("body.language = python", body.get("language") == "python")
    check("body.executor = kernel:python", body.get("executor") == "kernel:python")
    check("body.exit_code = 0", body.get("exit_code") == 0)
    check("body.stdout_tail contains 'answer is 3'",
          "answer is 3" in (body.get("stdout_tail") or ""))
    check("body.wall_time_s > 0",
          isinstance(body.get("wall_time_s"), (int, float)) and body["wall_time_s"] > 0)
    check("body.package_versions is dict and non-empty",
          isinstance(body.get("package_versions"), dict) and len(body["package_versions"]) > 0)
    check("body.env_fingerprint is sha256",
          (body.get("env_fingerprint") or "").startswith("sha256:"))
    check("body.language_version looks like X.Y.Z",
          (body.get("language_version") or "").count(".") >= 1)
    check("body.produced is a list", isinstance(body.get("produced"), list))

    print("\n[D] list_by_thread surfaces this exec")
    by_thread = exec_records.list_by_thread(thread_id)
    check("list_by_thread returned >=1", len(by_thread) >= 1)
    check("exec_id appears in list_by_thread",
          any(rec["exec_id"] == eid for rec in by_thread))

    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS INTEGRATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
