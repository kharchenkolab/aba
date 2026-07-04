"""Job archive + auto-retention (Item 1). A terminal job can be dismissed (soft archive,
provenance kept), archived jobs drop out of list_jobs but are still fetchable by id, active
jobs can't be dismissed, and prune_terminal_jobs caps the visible terminal jobs per project."""
import os
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_DB_PATH", str(Path(tempfile.mkdtemp(prefix="aba_jobsarch_")) / "t.db"))

from core.graph._schema import init_db, _project_conn  # noqa: E402

init_db()

from core.graph.jobs import (  # noqa: E402
    create_job, get_job, list_jobs, update_job, archive_job, prune_terminal_jobs,
)


def _clean():
    with _project_conn(None) as c:
        c.execute("DELETE FROM jobs")
        c.commit()


try:  # pytest is optional (base runtime env doesn't ship it); __main__ runs standalone
    import pytest

    @pytest.fixture(autouse=True)
    def _fresh():
        _clean()
        yield
except ImportError:
    pytest = None


def _mk(jid, status="done"):
    create_job(jid, "run_python", jid, None, {})
    if status != "queued":
        update_job(jid, status=status)


def test_archive_hides_from_list_keeps_get_and_optin():
    _mk("j_done", "done")
    assert any(j["id"] == "j_done" for j in list_jobs())          # visible before
    assert archive_job("j_done") is True
    assert all(j["id"] != "j_done" for j in list_jobs())          # hidden after
    assert get_job("j_done") is not None                          # still fetchable by id
    assert any(j["id"] == "j_done" for j in list_jobs(include_archived=True))  # opt-in shows it


def test_archive_refuses_active_job():
    _mk("j_run", "running")
    assert archive_job("j_run") is False                          # can't dismiss a running job
    assert any(j["id"] == "j_run" for j in list_jobs())


def test_archive_idempotent():
    _mk("j2", "failed")
    assert archive_job("j2") is True
    assert archive_job("j2") is False                             # already archived → no-op


def test_prune_keeps_newest_N_terminal_untouches_active():
    for i in range(5):
        _mk(f"t{i}", "done")
    _mk("act", "running")
    assert prune_terminal_jobs(keep=3) == 2                       # 5 terminal - keep 3 = 2 archived
    vis = list_jobs()
    assert any(j["id"] == "act" for j in vis)                     # active untouched
    assert len([j for j in vis if j["status"] == "done"]) == 3    # newest 3 terminal kept
    assert prune_terminal_jobs(keep=3) == 0                       # idempotent


if __name__ == "__main__":  # runnable without pytest (base env lacks it)
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        _clean()
        fn()
        print("PASS", fn.__name__)
    print(f"all {len(fns)} passed")
