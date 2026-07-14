"""Fix #2 — runner writes run.log to the job's work dir.

Direct unit test of _write_job_run_log; the full runner path is exercised
elsewhere and would require a live event loop to test end-to-end.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_runlog_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.jobs.runner import _write_job_run_log  # noqa: E402


def test_writes_log_to_result_obj_cwd():
    workdir = Path(tempfile.mkdtemp())
    _write_job_run_log(
        result_obj={"cwd": str(workdir), "returncode": 0},
        stdout="hello world\n", stderr="", job_id="job_x", project_id="prj_x",
    )
    log = workdir / "run.log"
    assert log.exists()
    body = log.read_text()
    assert "STDOUT" in body and "hello world" in body
    assert "EXIT 0" in body


def test_writes_log_with_stderr():
    workdir = Path(tempfile.mkdtemp())
    _write_job_run_log(
        result_obj={"cwd": str(workdir), "returncode": 1},
        stdout="some out\n", stderr="boom error\n",
        job_id="job_y", project_id="prj_y",
    )
    body = (workdir / "run.log").read_text()
    assert "STDERR" in body
    assert "boom error" in body
    assert "EXIT 1" in body


def test_omits_empty_streams():
    workdir = Path(tempfile.mkdtemp())
    _write_job_run_log(
        result_obj={"cwd": str(workdir), "returncode": 0},
        stdout="", stderr="", job_id="job_z", project_id="prj_z",
    )
    body = (workdir / "run.log").read_text()
    assert "STDOUT" not in body
    assert "STDERR" not in body
    assert "EXIT 0" in body


def test_falls_back_to_project_work_dir_when_cwd_missing():
    """If result_obj lacks cwd, use project_work_dir(project_id)/job_id."""
    from core.config import project_work_dir
    pid = "prj_fallback"
    expected = project_work_dir(pid) / "job_fb"
    expected.parent.mkdir(parents=True, exist_ok=True)
    _write_job_run_log(
        result_obj={"returncode": 0},
        stdout="fallback log\n", stderr="",
        job_id="job_fb", project_id=pid,
    )
    log = expected / "run.log"
    assert log.exists()
    assert "fallback log" in log.read_text()


# ─── runner ─────────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
