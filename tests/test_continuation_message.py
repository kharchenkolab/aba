"""Fix #1 — continuation message reflects actual outcome.

Three branches must produce distinct, accurate messages:
- failed: explicit error text
- done with N artifacts: "N new artifacts registered, continue"
- done with 0 artifacts: "finished — but no new artifacts; investigate"
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_contmsg_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.jobs.continuation import _continuation_message_text  # noqa: E402


def _setup_project_with_entities(pid: str, n_artifacts: int,
                                 started_at: str = "2026-06-08T18:14:18+00:00") -> None:
    """Create a project DB with N figure entities whose created_at >= started_at."""
    import sqlite3
    from core.config import project_db_path, project_root
    project_root(pid).mkdir(parents=True, exist_ok=True)
    db = project_db_path(pid)
    from core.graph import _schema as _sm
    prev = _sm.DB_PATH
    try:
        _sm.set_db_path(db)
        _sm.init_db()
    finally:
        _sm.set_db_path(prev)
    c = sqlite3.connect(db)
    later = "2026-06-08T18:15:00+00:00"
    earlier = "2026-06-08T18:00:00+00:00"
    for i in range(n_artifacts):
        c.execute(
            "INSERT INTO entities (id, type, title, status, created_at, updated_at) "
            "VALUES (?, 'figure', ?, 'active', ?, ?)",
            (f"fig_{i}", f"fig{i}", later, later),
        )
    # Also insert a pre-job entity that should NOT count
    c.execute(
        "INSERT INTO entities (id, type, title, status, created_at, updated_at) "
        "VALUES ('fig_pre', 'figure', 'old', 'active', ?, ?)",
        (earlier, earlier),
    )
    c.commit()
    c.close()


def test_failed_branch_includes_error():
    msg = _continuation_message_text({
        "id": "job_x", "title": "Test", "status": "failed",
        "error": "Rscript failed at line 42",
    })
    assert "FAILED" in msg
    assert "job_x" in msg
    assert "Rscript failed at line 42" in msg
    assert "didn't silently move on" not in msg  # don't reverse-test the negation


def test_done_with_artifacts_says_count():
    _setup_project_with_entities("prj_done_n", 5, "2026-06-08T18:14:18+00:00")
    msg = _continuation_message_text({
        "id": "job_y", "title": "All worked", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id="prj_done_n")
    assert "finished" in msg
    assert "5 new artifacts registered" in msg
    assert "Continue with the next step" in msg


def test_done_with_zero_artifacts_warns_silent_failure():
    _setup_project_with_entities("prj_done_zero", 0, "2026-06-08T18:14:18+00:00")
    msg = _continuation_message_text({
        "id": "job_z", "title": "Empty run", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id="prj_done_zero")
    assert "no new artifacts were registered" in msg
    # CRITICAL: must NOT contain the false "artifacts are registered" claim
    assert "artifacts are registered to this thread's Run" not in msg
    # Should suggest investigation
    assert "run.log" in msg


def test_done_singular_message_for_one_artifact():
    _setup_project_with_entities("prj_done_one", 1, "2026-06-08T18:14:18+00:00")
    msg = _continuation_message_text({
        "id": "job_one", "title": "One produced", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id="prj_done_one")
    assert "1 new artifact registered" in msg
    assert "1 new artifacts" not in msg


def test_done_with_log_tail_included_in_zero_branch():
    _setup_project_with_entities("prj_done_tail", 0, "2026-06-08T18:14:18+00:00")
    msg = _continuation_message_text({
        "id": "job_t", "title": "tail-bearing", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
        "log_tail": "ARGUMENT 'foo.R' __ignored__",
    }, project_id="prj_done_tail")
    assert "Log tail" in msg
    assert "ARGUMENT 'foo.R' __ignored__" in msg


def test_done_handles_missing_project_id_gracefully():
    """If we can't query the DB, fall back to the zero-branch (safer than
    claiming success that we can't verify)."""
    msg = _continuation_message_text({
        "id": "job_q", "title": "no pid", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id=None)
    assert "no new artifacts were registered" in msg


# ─── Fix #6 — surface job work dir + file list ──────────────────────────────
def _create_job_work_dir_with_files(pid: str, job_id: str,
                                    filenames: list[str]) -> str:
    """Create <work>/<job_id>/ with the given files. Returns its abs path."""
    from core.config import project_work_dir
    wd = project_work_dir(pid) / job_id
    wd.mkdir(parents=True, exist_ok=True)
    for n in filenames:
        (wd / n).write_text("stub")
    return str(wd)


def test_files_blurb_lists_job_outputs_with_absolute_paths():
    """The real bug: a background job left seurat_preprocessed.rds in
    <work>/job_<id>/, but the next R cell readRDS()'d the bare filename
    in <work>/ana_<id>/ and got 'gzfile: cannot open the connection'.
    The continuation message must give the agent the full path."""
    pid = "prj_files_paths"
    _setup_project_with_entities(pid, 0, "2026-06-08T18:14:18+00:00")
    job_id = "job_307f8db583"
    wd = _create_job_work_dir_with_files(pid, job_id,
        ["seurat_preprocessed.rds", "run.log", "script.R"])
    msg = _continuation_message_text({
        "id": job_id, "title": "Seurat preproc", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id=pid)
    # Must surface the absolute job work dir, not just a bare filename.
    assert wd in msg, f"expected {wd!r} in message; got: {msg}"
    assert "seurat_preprocessed.rds" in msg
    # Don't list housekeeping files — they're not pipeline outputs. We
    # check inside the file listing specifically (the prose may legitimately
    # mention "inspect run.log" as guidance — that's not a leak).
    assert f"- {wd}/run.log" not in msg
    assert f"- {wd}/script.R" not in msg
    # Warn about cwd mismatch explicitly.
    assert "absolute paths" in msg.lower()


def test_files_blurb_caps_long_listings():
    pid = "prj_files_cap"
    _setup_project_with_entities(pid, 0, "2026-06-08T18:14:18+00:00")
    job_id = "job_many"
    names = [f"out_{i:03d}.csv" for i in range(50)]
    _create_job_work_dir_with_files(pid, job_id, names)
    msg = _continuation_message_text({
        "id": job_id, "title": "many outputs", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id=pid)
    # First 20 should be listed by name
    assert "out_000.csv" in msg
    assert "out_019.csv" in msg
    # The 21st should NOT be (we capped at 20)
    assert "out_020.csv" not in msg
    # ...and the remainder should be summarized
    assert "30 more" in msg


def test_files_blurb_omitted_when_no_files():
    """No work dir present → no files_blurb (don't fabricate paths)."""
    pid = "prj_files_none"
    _setup_project_with_entities(pid, 0, "2026-06-08T18:14:18+00:00")
    msg = _continuation_message_text({
        "id": "job_nodir", "title": "no work dir", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id=pid)
    assert "Files written to" not in msg


def test_files_blurb_present_in_success_branch_too():
    """Even when N artifacts were registered, listing the raw files in the
    job dir is useful — a Seurat run may register some figures AND leave
    a giant .rds the agent should know how to find."""
    pid = "prj_files_success"
    _setup_project_with_entities(pid, 2, "2026-06-08T18:14:18+00:00")
    job_id = "job_mixed"
    _create_job_work_dir_with_files(pid, job_id, ["model.rds", "matrix.h5"])
    msg = _continuation_message_text({
        "id": job_id, "title": "mixed outputs", "status": "done",
        "started_at": "2026-06-08T18:14:18+00:00",
    }, project_id=pid)
    assert "2 new artifacts registered" in msg
    assert "model.rds" in msg
    assert "matrix.h5" in msg


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
