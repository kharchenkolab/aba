"""A weft row whose supervisor died must be stamped — remote sites included.

Measured on the docker slurm fixture with a REAL `kill -9` of the backend while a
cluster job was in flight:

  * the job ran to completion on the node — `exit_code: 0`, `result.json` written,
    `log: [aba-harness] ok`;
  * weft's `task_status` stayed **RUNNING** forever, because whatever advances it
    died with the controller;
  * so `poll()` saw a non-terminal state, returned None every cycle, and ABA's row
    sat at `running` for good — a finished job whose result was on the node the
    whole time, invisible.

`weft_submitter.poll` already knows how to survive a frozen state: it reads
`result.json` as the durable truth. That path is gated on `params.local_orphan_at`,
and `reconcile_jobs` only ever stamped rows whose site was `local` — remote ones
were skipped with the comment "remote lanes genuinely survive a restart". The WORK
does survive; the BOOKKEEPING did not.

Why the simulated outage in regtest's mn_restart_* cases could not see this: with
the controller alive, weft's state advances normally and the task reaches DONE, so
the frozen-state path is never entered. It takes a real process death.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture
def single_db(tmp_path, monkeypatch):
    """A SINGLE-mode jobs DB, the shape reconcile walks in tests/dev installs."""
    from core import projects
    from core.graph import _schema
    from core import config

    db = tmp_path / "jobs.db"
    monkeypatch.setattr(_schema, "DB_PATH", db)
    monkeypatch.setattr(projects, "SINGLE", True, raising=False)
    monkeypatch.setattr(projects, "_single", lambda: True)
    # Setting objects are frozen, so swap the whole descriptor rather than its
    # .get — reconcile reads settings.db_path.get() to find the SINGLE-mode DB.
    class _P:
        @staticmethod
        def get():
            return str(db)
    monkeypatch.setattr(config.settings, "db_path", _P)
    _schema.init_db()
    return db


def _row(db: Path, jid: str, params: dict, status: str = "running"):
    c = sqlite3.connect(db)
    c.execute("INSERT INTO jobs (id, kind, title, status, params, created_at) "
              "VALUES (?,?,?,?,?,?)",
              (jid, "run_python", jid, status, json.dumps(params),
               "2026-07-31T00:00:00+00:00"))
    c.commit(); c.close()


def _params(db: Path, jid: str) -> dict:
    c = sqlite3.connect(db)
    r = c.execute("SELECT params FROM jobs WHERE id=?", (jid,)).fetchone()
    c.close()
    return json.loads(r[0] or "{}") if r else {}


def _status(db: Path, jid: str) -> str:
    c = sqlite3.connect(db)
    r = c.execute("SELECT status FROM jobs WHERE id=?", (jid,)).fetchone()
    c.close()
    return r[0] if r else ""


BASE = {"submitter": "weft", "weft_id": "jb_x", "timeout_s": 300}


# ── the regression ───────────────────────────────────────────────────────────

def test_a_REMOTE_weft_row_is_stamped(single_db):
    """THE bug: a cluster job's row was skipped, so poll's result.json path — the
    only thing that can finish a job whose task state is frozen — stayed locked."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_remote", {**BASE, "weft_site": "hpc"})
    reconcile_jobs()
    assert _params(single_db, "job_remote").get("local_orphan_at"), \
        "a remote weft row was not stamped — its frozen task state can never resolve"


def test_a_row_carrying_only_site_is_stamped(single_db):
    """WIDE: the site lands under `site` on some paths and `weft_site` on others."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_site_key", {**BASE, "site": "cbe"})
    reconcile_jobs()
    assert _params(single_db, "job_site_key").get("local_orphan_at")


def test_the_LOCAL_row_is_still_stamped(single_db):
    """CEILING: the behaviour that already worked must not regress."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_local", {**BASE, "weft_site": "local"})
    reconcile_jobs()
    assert _params(single_db, "job_local").get("local_orphan_at")


def test_a_row_with_no_site_is_stamped(single_db):
    """WIDE — the degenerate shape: no site key at all (treated as local)."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_nosite", dict(BASE))
    reconcile_jobs()
    assert _params(single_db, "job_nosite").get("local_orphan_at")


# ── the stamp must stay narrow ────────────────────────────────────────────────

def test_a_row_without_a_task_id_is_NOT_stamped_but_reaped(single_db):
    """CEILING: no `weft_id` means nothing reached the substrate — there is no
    result.json to wait for, so it must be reaped, not stamped and waited on."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_notask",
         {"submitter": "weft", "sync": True, "weft_site": "hpc"})
    reconcile_jobs()
    assert not _params(single_db, "job_notask").get("local_orphan_at")
    assert _status(single_db, "job_notask") == "failed"


def test_an_already_stamped_row_keeps_its_ORIGINAL_timestamp(single_db):
    """Idempotence with teeth: re-stamping would push the walltime deadline out on
    every boot, so a wordless orphan could never time out. reconcile_jobs is
    documented as safe to call repeatedly."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_twice", {**BASE, "weft_site": "hpc",
                                  "local_orphan_at": 1000.0})
    reconcile_jobs()
    assert _params(single_db, "job_twice")["local_orphan_at"] == 1000.0


def test_a_non_weft_row_is_not_stamped(single_db):
    """A local-worker row is reaped by step 1; the weft stamp must not touch it."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_plain", {"timeout_s": 300})
    reconcile_jobs()
    assert not _params(single_db, "job_plain").get("local_orphan_at")


def test_the_stamp_is_a_usable_timestamp(single_db):
    """poll() does arithmetic on it (`time.time() - float(oat) < deadline`), so a
    non-numeric stamp would raise inside the poll loop."""
    from core.jobs.runner import reconcile_jobs
    _row(single_db, "job_ts", {**BASE, "weft_site": "hpc"})
    before = time.time()
    reconcile_jobs()
    oat = float(_params(single_db, "job_ts")["local_orphan_at"])
    assert before - 5 <= oat <= time.time() + 5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── layer 2: the stamp is useless if the result is looked for on the wrong side ─
#
# The stamp only OPENS the recovery path; the path then has to find result.json.
# `_run_dir` is a CONTROLLER directory, but a DETACHED job writes result.json on
# the NODE and it travels over the data plane. So stamping remote rows without
# fixing the lookup turned "hangs forever" into "fails honestly after walltime"
# with the answer sitting on the node — measured on the fixture: exit_code 0,
# result.json present there, weft frozen at RUNNING, row failed anyway.
#
# The layer-1 guards above all still passed at that point, because they assert the
# STAMP APPEARS rather than that recovery SUCCEEDS — output, not action.

class _Sub:
    """WeftSubmitter with only what these two methods touch."""
    def __init__(self, run_dir):
        self._rd = run_dir

    def _run_dir(self, job):
        return self._rd


def _present(run_dir, params, *, remote_bytes=None, raises=False):
    from core.jobs.weft_submitter import WeftSubmitter
    from core.compute import retention
    calls = []

    def fake_read(target, rel, max_bytes=None):
        calls.append((target, rel))
        if raises:
            raise RuntimeError("substrate hiccup")
        return {"bytes_b64": remote_bytes} if remote_bytes else {}

    orig = retention.file_read
    retention.file_read = fake_read
    try:
        got = WeftSubmitter._detached_result_present(_Sub(run_dir), {}, params)
    finally:
        retention.file_read = orig
    return got, calls


REMOTE = {"weft_id": "jb_x", "weft_site": "hpc"}
LOCAL = {"weft_id": "jb_x", "weft_site": "local"}


def test_a_detached_result_is_found_ON_THE_NODE(tmp_path):
    """THE layer-2 bug: nothing on the controller, result.json on the node."""
    got, calls = _present(tmp_path, REMOTE, remote_bytes="eyJ9")
    assert got is True, "a remote result.json was not found — recovery stalls"
    assert calls and calls[0][1] == "result.json", calls


def test_a_missing_remote_result_is_not_invented(tmp_path):
    """CEILING: no result yet must stay False, or the walltime rule is bypassed
    and a mid-run job is declared finished."""
    got, _ = _present(tmp_path, REMOTE, remote_bytes=None)
    assert got is False


def test_a_substrate_hiccup_answers_NOT_YET(tmp_path):
    """WIDE: an unreadable substrate must not fabricate a verdict in either
    direction — "not yet" lets the walltime rule decide."""
    got, _ = _present(tmp_path, REMOTE, raises=True)
    assert got is False


def test_the_LOCAL_lane_never_asks_the_substrate(tmp_path):
    """CEILING: a local job's file is right there; a needless data-plane call per
    poll cycle would be pure cost."""
    (tmp_path / "result.json").write_text("{}")
    got, calls = _present(tmp_path, LOCAL)
    assert got is True and calls == [], calls


def test_a_local_row_with_no_file_does_not_probe_remotely(tmp_path):
    got, calls = _present(tmp_path, LOCAL)
    assert got is False and calls == []


# ── a scheduler kill must not read like a user cancel ─────────────────────────

def test_a_user_cancel_says_so():
    from core.jobs.weft_submitter import WeftSubmitter
    note = WeftSubmitter._cancelled_note({"cancel_requested": True,
                                          "weft_site": "hpc"})
    assert "your request" in note and "hpc" in note


def test_a_scheduler_kill_says_it_was_NOT_the_user():
    """The row carries no cancel intent, so the outcome must name the scheduler
    and the likely reasons — the two imply opposite next actions."""
    note = WeftSubmitter_note = None
    from core.jobs.weft_submitter import WeftSubmitter
    note = WeftSubmitter._cancelled_note({"weft_site": "hpc"})
    assert "not by you" in note
    assert "walltime" in note and "re-submit" in note
    assert "your request" not in note


def test_the_two_notes_are_actually_different():
    """ARMED: both were once the same string, which is the defect."""
    from core.jobs.weft_submitter import WeftSubmitter
    a = WeftSubmitter._cancelled_note({"cancel_requested": True})
    b = WeftSubmitter._cancelled_note({})
    assert a != b
