"""Background-job failure handling (2026-06-28 live incident, prj_0590c5d8):

  Issue B — a worker-level crash (run_python_code itself throws) must mark the
  job failed AND fire the continuation so the agent's plan resumes. Previously
  the continuation was wired only into _finalize_job, so an exception bypassed it
  and the agent was left blind.

  Issue A guard — an empty interpreter/cwd reaching Popen must raise a clear,
  diagnosable error instead of the cryptic `PermissionError: [Errno 13] ... ''`.

Run:  .venv/bin/python tests/test_jobs_failure_notify.py
"""
from __future__ import annotations
import os
import sys
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_jobfail_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "j.db")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db          # noqa: E402
from core.graph import jobs                       # noqa: E402
from core.jobs import runner                      # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_issue_b_worker_crash_notifies():
    print("Issue B: worker crash marks failed + fires the continuation")
    jobs.create_job("job_fail1", "run_python", "t", None, {"code": "x=1", "project_id": "default"})

    # run_python_code is imported late inside _run_one — patch the module attr so
    # the crash path (the live empty-path PermissionError) is exercised.
    import core.exec.run as _run
    _orig = _run.run_python_code
    _run.run_python_code = lambda *a, **k: (_ for _ in ()).throw(
        PermissionError(13, "Permission denied", ""))

    fired = {}
    _orig_cont = runner._continue_after_failure
    async def _fake_continue(job_id, lookup_pid, effective_pid):
        fired["call"] = (job_id, effective_pid)
    runner._continue_after_failure = _fake_continue
    try:
        asyncio.run(runner._run_one("job_fail1", "default"))
    finally:
        _run.run_python_code = _orig
        runner._continue_after_failure = _orig_cont

    job = jobs.get_job("job_fail1") or {}
    check("job marked failed", job.get("status") == "failed", str(job.get("status")))
    check("worker-exception recorded in error", "worker exception" in (job.get("error") or ""), str(job.get("error")))
    check("full traceback captured in log_tail (Issue A instrument)",
          "Traceback" in (job.get("log_tail") or ""), (job.get("log_tail") or "")[:80])
    check("continuation FIRED on worker crash (Issue B fix)", fired.get("call") is not None)
    check("continuation got the right job", (fired.get("call") or (None,))[0] == "job_fail1")


def test_issue_a_empty_interp_guard():
    print("Issue A guard: empty interpreter/cwd → clear ValueError, not Popen ''")
    from core.exec.local import LocalSubprocessExecutor

    class _Env:
        env_overlay = None
        python = ""

    ex = LocalSubprocessExecutor()
    try:
        ex.exec(_Env(), ["", "/tmp/script.py"], cwd="/tmp", timeout_s=5)
        check("empty interpreter raises", False, "no error")
    except ValueError as e:
        check("empty interpreter → clear ValueError naming it", "empty interpreter" in str(e), str(e))
    except Exception as e:  # noqa: BLE001
        check("empty interpreter → ValueError (not the cryptic Popen error)", False, repr(e))

    try:
        ex.exec(_Env(), ["/usr/bin/python3", "/tmp/script.py"], cwd="", timeout_s=5)
        check("empty cwd raises", False, "no error")
    except ValueError as e:
        check("empty cwd → clear ValueError naming it", "empty cwd" in str(e), str(e))
    except Exception as e:  # noqa: BLE001
        check("empty cwd → ValueError", False, repr(e))


def test_sys_executable_recovery():
    print("Root cause: ensure_sys_executable() recovers an empty sys.executable")
    from core.exec.env_integrity import ensure_sys_executable
    _orig = sys.executable
    try:
        sys.executable = ""  # simulate the bare-argv[0] execve launch
        got = ensure_sys_executable()
        check("recovers a real interpreter path", bool(got) and os.path.exists(got), repr(got))
        check("patches sys.executable process-wide", sys.executable == got and bool(sys.executable))
    finally:
        if not sys.executable:
            sys.executable = _orig
    check("idempotent when already set", ensure_sys_executable() == sys.executable and bool(sys.executable))


def main() -> int:
    init_db()
    test_sys_executable_recovery()
    test_issue_b_worker_crash_notifies()
    test_issue_a_empty_interp_guard()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL JOB-FAILURE-NOTIFY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
