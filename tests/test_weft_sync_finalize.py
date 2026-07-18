"""Detached sync-run finalize honesty (the live first-use incident).

A synchronous remote step on a detached site completed on the substrate, but a
mid-wait server restart orphaned the in-tool waiter; the row was later
finalized down the SHARED-FS branch (stale job dict → no `detached` flag →
controller-local result.json check → fabricated "infra failure before the
entry ran"), then a second finalize flipped status to done while keeping the
stale error. Guards:

  1. poll() re-reads the persisted row: a stale caller dict cannot route a
     detached job down the shared-fs branch; a detached-contract site can't
     take that branch even when the row predates the `detached` stamp.
  2. Terminal DONE + unreadable result = grace retries, then an HONEST error
     (the entry ran; payload unreadable) — never "infra failure before the
     entry ran".
  3. _finalize_job is single-verdict: a second finalize of a terminal row is
     ignored (WARNING), and a success verdict clears any stale error.
  4. reconcile adopts orphaned sync weft rows (substrate-accepted → poll-loop
     ownership; never-submitted → reaped failed). The poll loop then sees them.

Run: python tests/test_weft_sync_finalize.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_syncfin_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
os.environ.pop("ABA_DB_PATH", None)
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core import projects  # noqa: E402

projects.init()
_PID = projects.create_project("SyncFinalize")["id"]
projects.set_current(_PID)

from core.graph.jobs import create_job, get_job, update_job  # noqa: E402
import core.jobs.weft_submitter as ws  # noqa: E402
import core.jobs.runner as runner  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


class _FakeAdapter:
    """task_status returns a fixed terminal state; task_result gives a log tail."""
    def __init__(self, state="DONE"):
        self.state = state

    def sync_call(self, name, *a, **k):
        if name == "task_status":
            return [{"state": self.state}]
        if name == "task_result":
            return {"logs": {"tail": "entry ran to completion"}, "node": "n1"}
        raise RuntimeError(f"unexpected substrate call {name}")


def _mk_row(**extra):
    jid = "job_" + uuid.uuid4().hex[:8]
    params = {"submitter": "weft", "weft_id": "w_" + jid, "project_id": _PID,
              "sync": True, "detached": True, "weft_site": "hpc", **extra}
    create_job(jid, "run_python", "t", None, params, project_id=_PID)
    return jid, params


# ── 1. stale caller dict cannot reach the shared-fs branch ───────────────────

def test_stale_dict_takes_detached_branch():
    jid, params = _mk_row()
    sub = ws.WeftSubmitter(site="hpc")
    hit = {}
    orig_adapter, ws._adapter = ws._adapter, lambda: _FakeAdapter("DONE")
    orig_pd = ws.WeftSubmitter._poll_detached
    ws.WeftSubmitter._poll_detached = lambda self, job, p, wid, st: hit.update(
        wid=wid, state=st) or {"status": "ok", "stdout": "", "returncode": 0}
    try:
        # the STALE dict a dead waiter (or pre-submit snapshot) would hold:
        # weft_id present, detached/sync ABSENT
        stale = {"id": jid, "project_id": _PID,
                 "params": {"weft_id": params["weft_id"]}}
        res = sub.poll(stale)
    finally:
        ws._adapter = orig_adapter
        ws.WeftSubmitter._poll_detached = orig_pd
    check("stale dict routed to the detached branch", hit.get("wid") == params["weft_id"], str(res))
    check("no fabricated shared-fs failure", not (res or {}).get("error"), str(res))


def test_site_contract_fallback_when_row_predates_stamp():
    # row genuinely lacks `detached` (legacy/interrupted submit) — the SITE's
    # declared contract must still keep it off the shared-fs branch
    jid, params = _mk_row()
    p2 = dict(params); p2.pop("detached")
    update_job(jid, project_id=_PID, params=p2)
    sub = ws.WeftSubmitter(site="hpc")
    hit = {}
    orig_adapter, ws._adapter = ws._adapter, lambda: _FakeAdapter("DONE")
    orig_sc, ws.site_contract = ws.site_contract, lambda s: "detached"
    orig_pd = ws.WeftSubmitter._poll_detached
    ws.WeftSubmitter._poll_detached = lambda self, job, p, wid, st: hit.update(
        wid=wid) or {"status": "ok", "stdout": "", "returncode": 0}
    try:
        res = sub.poll(get_job(jid, project_id=_PID))
    finally:
        ws._adapter = orig_adapter
        ws.site_contract = orig_sc
        ws.WeftSubmitter._poll_detached = orig_pd
    check("contract-detached site avoids shared-fs branch", hit.get("wid") == p2["weft_id"], str(res))


# ── 2. DONE + unreadable result: grace retries, then honest error ────────────

def test_done_unreadable_result_grace_then_honest():
    jid, params = _mk_row()
    sub = ws.WeftSubmitter(site="hpc")
    from core.compute import retention
    orig_fr = retention.file_read
    retention.file_read = lambda *a, **k: (_ for _ in ()).throw(OSError("data plane down"))
    orig_adapter, ws._adapter = ws._adapter, lambda: _FakeAdapter("DONE")
    try:
        row = get_job(jid, project_id=_PID)
        outs = [sub.poll(row) for _ in range(ws._RESULT_READ_RETRIES)]
        final = sub.poll(row)
    finally:
        retention.file_read = orig_fr
        ws._adapter = orig_adapter
    check("grace polls return None (retry, no verdict)", all(o is None for o in outs), str(outs))
    check("verdict eventually returned", isinstance(final, dict) and "error" in final, str(final))
    err = (final or {}).get("error") or ""
    check("honest: says the entry ran / payload unreadable",
          "completed on the compute substrate" in err and "could not be read" in err, err[:160])
    check("never claims infra failure before the entry ran",
          "infra failure" not in err and "before the harness could run" not in err, err[:160])
    check("carries the node log tail", "entry ran to completion" in err, err[:200])


# ── 3. single-verdict finalize + error cleared on done ───────────────────────

def test_second_finalize_ignored():
    jid, _ = _mk_row()
    update_job(jid, project_id=_PID, status="done", error=None)
    asyncio.run(runner._finalize_job(get_job(jid, project_id=_PID),
                                     {"error": "late fabricated failure"},
                                     _PID, _PID))
    row = get_job(jid, project_id=_PID)
    check("terminal status survives a late failure verdict", row["status"] == "done", str(row["status"]))
    check("no stale error grafted on", not row.get("error"), str(row.get("error")))


def test_success_finalize_clears_stale_error():
    jid, _ = _mk_row()
    # a non-terminal row carrying a stale error from an earlier aborted pass
    update_job(jid, project_id=_PID, status="running", error="stale infra failure")
    orig_dispatch, runner.dispatch = runner.dispatch, lambda *a, **k: None
    orig_wer = runner._write_exec_record_for_job
    runner._write_exec_record_for_job = lambda *a, **k: None
    try:
        asyncio.run(runner._finalize_job(get_job(jid, project_id=_PID),
                                         {"stdout": "ok", "returncode": 0},
                                         _PID, _PID))
    finally:
        runner.dispatch = orig_dispatch
        runner._write_exec_record_for_job = orig_wer
    row = get_job(jid, project_id=_PID)
    check("row finalized done", row["status"] == "done", str(row["status"]))
    check("stale error cleared on success", not row.get("error"), str(row.get("error")))


# ── 4. reconcile adopts orphaned sync rows; poll loop sees them ──────────────

def test_reconcile_adopts_orphaned_sync_rows():
    jid_sub, _ = _mk_row()                        # reached the substrate
    update_job(jid_sub, project_id=_PID, status="running")
    jid_nosub = "job_" + uuid.uuid4().hex[:8]     # never reached the substrate
    create_job(jid_nosub, "run_python", "t", None,
               {"submitter": "weft", "sync": True, "project_id": _PID},
               project_id=_PID)
    stats = runner.reconcile_jobs()
    adopted = get_job(jid_sub, project_id=_PID)
    ap = adopted["params"]
    check("substrate-accepted sync row adopted (sync off)", ap.get("sync") is False, str(ap))
    check("adoption is stamped for provenance", ap.get("sync_orphaned") is True, str(ap))
    check("adopted row keeps its status for the poll loop",
          adopted["status"] == "running", adopted["status"])
    reaped = get_job(jid_nosub, project_id=_PID)
    check("never-submitted sync row reaped failed", reaped["status"] == "failed", reaped["status"])
    check("stats count the adoption", stats.get("adopted_sync_weft", 0) >= 1, str(stats))
    watched = {j["id"] for j in runner._active_weft_jobs()}
    check("poll loop now watches the adopted row", jid_sub in watched, str(watched))
    check("poll loop still ignores the reaped row", jid_nosub not in watched)


def _run():
    import traceback
    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
        except Exception:  # noqa: BLE001
            fails += 1
            print(f"  [FAIL] {name} raised:")
            traceback.print_exc()
    print(f"\n{'ALL PASS' if not (fails or _failures) else f'FAILED ({fails + len(_failures)})'}")
    return 1 if (fails or _failures) else 0


if __name__ == "__main__":
    raise SystemExit(_run())
