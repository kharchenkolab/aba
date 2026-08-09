"""A site outage during a detached background job is NOT a job verdict.

Found live (regtest `mn_net_drop_midjob`, 2026-08-09): sshd was cut on the
fixture 15 s into a background job. The substrate's rows flipped the task to
FAILED with `site.unreachable` — whose own hints said "a retry builds a fresh
connection" — and `_poll_detached` finalized the ABA job as failed while the
node computed the true answer. Silently-wrong family, network edition: the
WORK survived the outage; the BOOKKEEPING declared it dead.

The contract now, mirroring restart-survival doctrine (`result.json` is the
durable truth; walltime bounds the wait):

  * FAILED + a TRANSPORT-class recorded error → an OUTAGE, not a verdict:
    poll answers "not finished yet", stamping `transport_outage_at` once;
  * the moment transport returns, the result.json read finalizes the REAL
    outcome — a stale FAILED row cannot override bytes the job wrote;
  * past walltime + grace, the verdict is an honest CONNECTIVITY failure that
    names the site and says the work may be intact — never "the task failed";
  * a REAL task failure (non-transport error) still fails immediately — the
    outage window must not delay honest verdicts (ceiling).
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_RT = tempfile.mkdtemp(prefix="aba_outage_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

TRANSPORT_ERR = {"error": "site.unreachable",
                 "detail": "ssh transport to hpc failed",
                 "hints": {"delivered": "unknown",
                           "note": "connection multiplexer reset; a retry "
                                   "builds a fresh connection"},
                 "retryable": True}
REAL_ERR = {"error": "task.failed", "detail": "exit 1: assertion failed"}


# ── the payload classifier ───────────────────────────────────────────────────

@pytest.mark.parametrize("payload,expect", [
    (TRANSPORT_ERR, True),
    ({"error": "infra.timeout", "detail": "x"}, True),
    ({"error": "task.failed"}, False),
    ({"error": "env.solve_conflict"}, False),
    # retryable-but-delivered: the far side got it — its verdict stands
    ({"error": "x.y", "retryable": True, "hints": {"delivered": "yes"}}, False),
    ({"error": "x.y", "retryable": True, "hints": {"delivered": "unknown"}}, True),
    (None, False),
    ("site.unreachable", False),          # a bare string is not the typed shape
])
def test_the_payload_classifier(payload, expect):
    from core.jobs.weft_submitter import _payload_transport_error
    assert _payload_transport_error(payload) is expect


# ── the poll behaviour ───────────────────────────────────────────────────────

def _sub(monkeypatch, *, task_err, result_bytes=None, dark=True):
    """A WeftSubmitter with only what _poll_detached touches, over a fake
    substrate: `task_status` returns `task_err` on the row; the data-plane
    read raises while `dark`, else serves `result_bytes`."""
    from core.jobs import weft_submitter as ws
    from core.compute import retention

    sub = object.__new__(ws.WeftSubmitter)
    sub._job_site = lambda params: "hpc"
    sub._compute_block = lambda wid, state: {"state": state}
    sub._result_miss = lambda wid: False
    sub._cancelled_note = lambda params: "n/a"

    class _A:
        def sync_call(self, name, *a, **kw):
            if name == "task_status":
                return [{"error": task_err}]
            raise RuntimeError(f"unexpected verb {name}")
    monkeypatch.setattr(ws, "_adapter", lambda: _A())

    def file_read(target, rel, max_bytes=None):
        if dark:
            raise RuntimeError("site dark")
        if result_bytes is not None and rel == "result.json":
            return {"bytes_b64": base64.b64encode(result_bytes).decode()}
        raise RuntimeError("no such file")
    monkeypatch.setattr(retention, "file_read", file_read)

    stamped = {}
    import core.graph.jobs as gj
    monkeypatch.setattr(gj, "update_job",
                        lambda jid, **kw: stamped.update(kw), raising=False)
    return sub, stamped


JOB = {"id": "job_x", "project_id": "prj_t"}


def test_THE_BUG_transport_failed_is_an_outage_not_a_verdict(monkeypatch):
    from core.jobs import weft_submitter as ws
    sub, stamped = _sub(monkeypatch, task_err=TRANSPORT_ERR, dark=True)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t"}
    out = ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_1", "FAILED")
    assert out is None, f"finalized a verdict during an outage: {out}"
    assert stamped.get("params", {}).get("transport_outage_at"), \
        "the outage window was never stamped — the wait has no bound"


def test_within_the_window_it_keeps_waiting(monkeypatch):
    from core.jobs import weft_submitter as ws
    sub, _ = _sub(monkeypatch, task_err=TRANSPORT_ERR, dark=True)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t",
              "transport_outage_at": time.time() - 60}
    assert ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_1", "FAILED") is None


def test_recovery_finalizes_the_REAL_result_over_the_stale_row(monkeypatch):
    """The durable-truth hatch: transport returns, result.json is there — the
    job finishes DONE with its true payload, FAILED row or not."""
    from core.jobs import weft_submitter as ws
    payload = json.dumps({"stdout": "8002000", "returncode": 0}).encode()
    sub, _ = _sub(monkeypatch, task_err=TRANSPORT_ERR,
                  result_bytes=payload, dark=False)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t",
              "transport_outage_at": time.time() - 60}
    out = ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_1", "FAILED")
    assert out is not None and "error" not in out, out
    assert out.get("returncode") == 0 and out.get("status") == "ok", out


def test_past_walltime_the_verdict_is_a_CONNECTIVITY_failure(monkeypatch):
    from core.jobs import weft_submitter as ws
    sub, _ = _sub(monkeypatch, task_err=TRANSPORT_ERR, dark=True)
    params = {"detached": True, "timeout_s": 60, "project_id": "prj_t",
              "transport_outage_at": time.time() - 60 - 181}
    out = ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_1", "FAILED")
    assert out and out.get("error"), out
    e = out["error"].lower()
    assert "connectivity" in e or "could not reach" in e, e
    assert "hpc" in e
    assert "may have completed" in e
    assert "task failed" not in e


def test_a_REAL_failure_is_not_delayed_by_the_window(monkeypatch):
    """CEILING: a genuine on-node failure keeps failing immediately — the
    outage path must key on the ERROR CLASS, not on state alone."""
    from core.jobs import weft_submitter as ws
    sub, stamped = _sub(monkeypatch, task_err=REAL_ERR, dark=True)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t"}
    out = ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_1", "FAILED")
    assert out is not None and out.get("error"), \
        "a real task failure was deferred by the outage window"
    assert not stamped.get("params", {}).get("transport_outage_at")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── never-delivered: the cut hit mid-staging ─────────────────────────────────

def _sub2(monkeypatch, *, jobdir_exists, submit_ok=True):
    """Outage already stamped; transport now answers file_stat. `jobdir_exists`
    drives the delivery receipt; task_submit records resubmissions."""
    from core.jobs import weft_submitter as ws
    from core.compute import retention
    from core.compute.errors import ComputeError

    sub = object.__new__(ws.WeftSubmitter)
    sub._job_site = lambda params: "hpc"
    sub._compute_block = lambda wid, state: {"state": state}
    sub._result_miss = lambda wid: False
    sub._cancelled_note = lambda params: "n/a"
    sub._build_detached_task = lambda job, params, env_id, site: {"t": 1}
    replaced = {}
    sub._record_run_target = lambda params, new, replace=None: replaced.update(
        new=new, old=replace)

    submitted = []

    class _A:
        def sync_call(self, name, *a, **kw):
            if name == "task_status":
                return [{"error": TRANSPORT_ERR}]
            if name == "task_submit":
                if not submit_ok:
                    raise ComputeError("site.unreachable", "still dark")
                submitted.append(a[0] if a else kw)
                return {"job_id": "jb_NEW"}
            raise RuntimeError(name)
    monkeypatch.setattr(ws, "_adapter", lambda: _A())

    def file_read(target, rel, max_bytes=None):
        raise RuntimeError("result not there")
    monkeypatch.setattr(retention, "file_read", file_read)

    def file_stat(target, rel):
        if jobdir_exists:
            return {"exists": True}
        raise ComputeError("data.missing", "no such jobdir")
    monkeypatch.setattr(retention, "file_stat", file_stat)

    stamped = {}
    import core.graph.jobs as gj
    monkeypatch.setattr(gj, "update_job",
                        lambda jid, **kw: stamped.update(kw), raising=False)
    return sub, submitted, stamped, replaced


def test_a_NEVER_DELIVERED_task_is_resubmitted_once(monkeypatch):
    """The rerun's shape: the cut hit mid-staging, no jobdir ever existed, and
    waiting for its result.json was a hang dressed as patience. data.missing
    on the driver machinery = never delivered = resubmit (nothing ran, so it
    is safe by definition)."""
    from core.jobs import weft_submitter as ws
    sub, submitted, stamped, replaced = _sub2(monkeypatch, jobdir_exists=False)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t",
              "transport_outage_at": time.time() - 30}
    out = ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_OLD", "FAILED")
    assert out is None
    assert submitted, "never-delivered task was not resubmitted"
    p = stamped.get("params") or {}
    assert p.get("weft_id") == "jb_NEW" and p.get("transport_resubmitted")
    assert replaced == {"new": "jb_NEW", "old": "jb_OLD"}


def test_a_DELIVERED_task_is_NOT_resubmitted(monkeypatch):
    """CEILING — the dangerous direction: the jobdir exists, the work may be
    mid-flight on the node; a resubmit would run it TWICE."""
    from core.jobs import weft_submitter as ws
    sub, submitted, _s, _r = _sub2(monkeypatch, jobdir_exists=True)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t",
              "transport_outage_at": time.time() - 30}
    assert ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_OLD", "FAILED") is None
    assert not submitted, "resubmitted a task whose jobdir exists on the node"


def test_resubmit_happens_at_most_ONCE(monkeypatch):
    from core.jobs import weft_submitter as ws
    sub, submitted, _s, _r = _sub2(monkeypatch, jobdir_exists=False)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t",
              "transport_outage_at": time.time() - 30,
              "transport_resubmitted": True}
    assert ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_OLD", "FAILED") is None
    assert not submitted, "a second resubmit — the once-bound is gone"


def test_a_dark_site_defers_the_delivery_question(monkeypatch):
    """WIDE: file_stat raising TRANSPORT (not data.missing) means still dark —
    neither wait-forever nor resubmit-blind; just the next cycle."""
    from core.jobs import weft_submitter as ws
    from core.compute import retention
    sub, submitted, _s, _r = _sub2(monkeypatch, jobdir_exists=False)

    def stat_dark(target, rel):
        raise RuntimeError("ssh transport failed")     # untyped transport raise
    monkeypatch.setattr(retention, "file_stat", stat_dark)
    params = {"detached": True, "timeout_s": 300, "project_id": "prj_t",
              "transport_outage_at": time.time() - 30}
    assert ws.WeftSubmitter._poll_detached(sub, JOB, params, "jb_OLD", "FAILED") is None
    assert not submitted, "resubmitted while the site was still dark"
