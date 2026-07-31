"""Restart-orphan recovery for LOCAL-lane weft jobs (misc/bug3): reconcile
stamps, the poll loop finalizes from result.json disk truth despite a frozen
substrate state, and single-DB mode is actually scanned at all."""
from __future__ import annotations

import json
import time

import pytest


def _mk_local_weft_row(status="running", extra=None):
    params = {"submitter": "weft", "weft_id": "jb_test1", "sync": False,
              "project_id": "single", "timeout_s": 120}
    params.update(extra or {})
    return {"id": "job_t1", "status": status, "error": None,
            "params": params}


class _FakeAdapter:
    def __init__(self, state="RUNNING"):
        self.state = state
        self.cancelled = []

    def sync_call(self, method, *a, **k):
        if method == "task_status":
            return [{"state": self.state}]
        if method == "task_cancel":
            self.cancelled.append(a[0])
            return {}
        if method == "task_result":
            return {}
        raise AssertionError(f"unexpected {method}")


@pytest.fixture()
def submitter(monkeypatch, tmp_path):
    from core.jobs import weft_submitter as ws
    fake = _FakeAdapter()
    monkeypatch.setattr(ws, "_adapter", lambda: fake)
    sub = ws.WeftSubmitter(site="local")
    monkeypatch.setattr(ws.WeftSubmitter, "_run_dir",
                        lambda self, job: tmp_path)
    monkeypatch.setattr(ws.WeftSubmitter, "_compute_block",
                        lambda self, wid, state: {"weft": {"task": wid}})
    return sub, fake, tmp_path


def test_unstamped_nonterminal_row_stays_pending(submitter):
    sub, fake, _ = submitter
    job = _mk_local_weft_row()
    assert sub.poll(job) is None


def test_stamped_row_finalizes_from_result_json(submitter, monkeypatch):
    """Frozen RUNNING substrate state + stamped orphan + result.json on disk
    → poll returns the TRUE result (the completed orphan's work is kept)."""
    sub, fake, run_dir = submitter
    (run_dir / "result.json").write_text(json.dumps(
        {"stdout": "RSTOTAL=59998\n", "exit_code": 0}))
    # fresh-row re-read goes through core.graph.jobs — return our row as-is
    import core.graph.jobs as gj
    job = _mk_local_weft_row(extra={"local_orphan_at": time.time() - 5})
    monkeypatch.setattr(gj, "get_job", lambda *a, **k: job)
    res = sub.poll(job)
    assert res is not None
    assert "RSTOTAL=59998" in (res.get("stdout") or "")
    assert not res.get("error")
    assert fake.cancelled == []          # nothing was killed


def test_stamped_row_past_deadline_fails_honestly(submitter):
    sub, fake, run_dir = submitter
    job = _mk_local_weft_row(extra={"local_orphan_at": time.time() - 9999})
    res = sub.poll(job)
    assert res is not None
    assert "orphaned by the restart" in (res.get("error") or "")
    assert fake.cancelled == ["jb_test1"]


def test_stamped_row_within_deadline_waits(submitter):
    sub, fake, run_dir = submitter
    job = _mk_local_weft_row(extra={"local_orphan_at": time.time() - 5})
    assert sub.poll(job) is None          # no result yet, not past walltime
    assert fake.cancelled == []


def test_reconcile_single_db_stamps_local_weft(tmp_path, monkeypatch):
    """SINGLE mode: reconcile must scan the flat DB (projects_scanned>=1) and
    stamp EVERY weft running row — the restart_study finding (projects_scanned: 0
    → orphan invisible forever).

    This test used to assert `not rows["j_rem"].get("local_orphan_at")` with the
    comment "remote survives". That encoded the same false premise as the code:
    measured on the docker slurm fixture with a real kill -9, a cluster job ran to
    completion (exit_code 0, result.json written on the node) while weft's
    task_status stayed frozen at RUNNING — so ABA's poll never finalized and the
    row hung forever. The WORK survives a restart; the BOOKKEEPING did not, and
    the stamp is what opens poll's result.json recovery path.

    Left as a two-sided check: both rows stamped, and the count matches, so a
    change that stamped nothing (or everything indiscriminately, including rows
    with no task id) still fails.
    """
    import sqlite3
    db = tmp_path / "flat.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, "
              "status TEXT, error TEXT, params TEXT, created_at TEXT, "
              "finished_at TEXT)")
    local = {"submitter": "weft", "weft_id": "jb_loc", "sync": False,
             "project_id": "single", "timeout_s": 60}
    remote = {"submitter": "weft", "weft_id": "jb_rem", "sync": False,
              "site": "hpc", "project_id": "single", "timeout_s": 60}
    c.execute("INSERT INTO jobs VALUES ('j_loc','run_python','running',"
              "NULL,?,'2026-01-01','')", (json.dumps(local),))
    c.execute("INSERT INTO jobs VALUES ('j_rem','run_python','running',"
              "NULL,?,'2026-01-01','')", (json.dumps(remote),))
    c.commit(); c.close()

    from core.jobs import runner as rn
    from core import projects as prj
    monkeypatch.setattr(prj, "SINGLE", True)
    monkeypatch.setenv("ABA_DB_PATH", str(db))
    monkeypatch.setattr(rn, "_reap_orphan_processes", lambda toks: 0)
    monkeypatch.setattr(rn, "_settle_job_deferred", lambda *a: None)
    stats = rn.reconcile_jobs()
    assert stats["projects_scanned"] == 1
    assert stats["stamped_local_orphans"] == 2      # local AND remote

    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    rows = {r["id"]: json.loads(r["params"])
            for r in c.execute("SELECT id, params FROM jobs")}
    c.close()
    assert rows["j_loc"].get("local_orphan_at")          # stamped
    # REMOTE is stamped too: its task state is advanced controller-side, so it
    # freezes at RUNNING when the backend dies (see the docstring).
    assert rows["j_rem"].get("local_orphan_at")
