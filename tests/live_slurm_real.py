"""REAL Slurm validation of the SlurmSubmitter — runs INSIDE a compute node
(dev_c1) against the dev OOD Slurm cluster, exercising the ACTUAL ABA code with
real sbatch/squeue/sacct/scancel.

Covers: submission, results (+ artifact harvest), resource flags, live monitoring
(squeue), cancellation (scancel), and errors. Job bodies avoid numpy (the el7
node's glibc 2.17 can't load the host-built numpy — an environment artifact, not
a code issue; on a real cluster the venv/SIF matches the node).

Run (from host):
  sg docker -c "docker exec dev_c1 /home/pkharchenko/aba/aba/.venv/bin/python \
      /home/pkharchenko/aba/aba/tests/live_slurm_real.py"
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
from pathlib import Path

# Runtime under the slurm_jobdir volume — visible to c1 AND c2, so a job
# scheduled on either node writes its sentinel where the poller reads it.
_RT = os.environ.setdefault("ABA_RUNTIME_DIR", "/data/abatest")
os.environ.setdefault("ABA_PROJECTS_DIR", _RT + "/projects")
os.environ.setdefault("ABA_WORK_DIR", _RT + "/work")
os.environ.setdefault("ARTIFACTS_DIR", _RT + "/artifacts")
os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
os.environ["ABA_HPC_CONFIG"] = _RT + "/hpc.yaml"
sys.path.insert(0, "/home/pkharchenko/aba/aba/backend")

Path(_RT).mkdir(parents=True, exist_ok=True)
# Dev nodes are 1-CPU containers with RealMemory=1000MB, so request 1 cpu and
# OMIT --mem (mem_gb:0 → no --mem flag; 1G would exceed 1000MB and be rejected).
Path(_RT + "/hpc.yaml").write_text(
    "partitions:\n"
    "  - {name: normal, max_cores: 1, max_mem_gb: 0, max_walltime_h: 1, gpu: false}\n"
    "defaults: {partition: normal, cores: 1, mem_gb: 0, walltime_h: 1}\n")

from core import projects                                              # noqa: E402
from core.graph.jobs import get_job                                    # noqa: E402
from core.graph.jobs import update_job                                  # noqa: E402
from core.jobs.runner import (submit_python_job, _finalize_job, cancel_job,  # noqa: E402
                              _slurm_poll_loop, reconcile_jobs)
from core.jobs.slurm_submitter import SlurmSubmitter                   # noqa: E402

projects.init()
_SUB = SlurmSubmitter()
_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}", flush=True)


def _loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop


def _drive_to_terminal(job_id: str, pid: str, timeout: int = 150) -> str:
    """Mimic the poll loop: poll → finalize until the row is terminal."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get_job(job_id, project_id=pid)
        if job["status"] in ("done", "failed", "cancelled"):
            return job["status"]
        job["project_id"] = pid
        res = _SUB.poll(job)
        if res is not None:
            _loop().run_until_complete(_finalize_job(job, res, pid, pid))
            return get_job(job_id, project_id=pid)["status"]
        time.sleep(2)
    return get_job(job_id, project_id=pid)["status"]


def _wait_running(job_id: str, pid: str, timeout: int = 60) -> dict:
    """Poll squeue (via info) until the job is RUNNING; return the info."""
    deadline = time.time() + timeout
    info: dict = {}
    while time.time() < deadline:
        info = _SUB.info(get_job(job_id, project_id=pid)) or {}
        if info.get("state") == "RUNNING":
            return info
        time.sleep(2)
    return info


# ── Scenario 1: submit → run on a node → result + artifact harvest ───────────
def s1_submit_result():
    pid = projects.create_project("s1")["id"]; projects.set_current(pid)
    code = "print('slurm-says-hi'); open('out.txt','w').write('artifact-data')"
    job = submit_python_job(code, "s1 result", None, project_id=pid, estimate={"runtime_min": 2})
    after = get_job(job["id"], project_id=pid)
    check("s1.submitted_real_sbatch", bool(after["params"].get("slurm_id")),
          f"slurm_id={after['params'].get('slurm_id')} run_dir={after['params'].get('run_dir')}")
    st = _drive_to_terminal(job["id"], pid)
    j = get_job(job["id"], project_id=pid)
    check("s1.status_done", st == "done", f"status={st}")
    check("s1.stdout_captured", "slurm-says-hi" in (j.get("log_tail") or ""),
          f"log_tail={(j.get('log_tail') or '')[:50]!r}")
    rd = after["params"].get("run_dir", "")
    check("s1.artifact_on_shared_fs", os.path.exists(os.path.join(rd, "out.txt")),
          f"out.txt under {rd}")


# ── Scenario 2: resource flags accepted by real Slurm ────────────────────────
def s2_resource_flags():
    pid = projects.create_project("s2")["id"]; projects.set_current(pid)
    # Asks for more than the node has → resolve_resources clamps to the partition
    # ceiling (1 cpu, no mem); real sbatch must accept the clamped request.
    job = submit_python_job("print('ok')", "s2 flags", None, project_id=pid,
                            estimate={"cores": 2, "mem_gb": 2, "runtime_min": 10})
    after = get_job(job["id"], project_id=pid)
    res = after["params"].get("resources") or {}
    check("s2.partition_normal", res.get("partition") == "normal", f"resources={res}")
    check("s2.clamped_to_node", res.get("cores") == 1 and res.get("mem_gb") == 0, f"resources={res}")
    st = _drive_to_terminal(job["id"], pid)
    check("s2.ran_after_clamp", st == "done", f"status={st} (real sbatch accepted the clamped flags)")


# ── Scenario 3: live monitoring via real squeue ──────────────────────────────
def s3_monitoring():
    pid = projects.create_project("s3")["id"]; projects.set_current(pid)
    job = submit_python_job("import time; time.sleep(18); print('woke')", "s3 monitor",
                            None, project_id=pid, estimate={"runtime_min": 5})
    info = _wait_running(job["id"], pid, timeout=60)
    check("s3.squeue_running", info.get("state") == "RUNNING", f"info={info}")
    check("s3.node_reported", bool(info.get("node")), f"node={info.get('node')}")
    st = _drive_to_terminal(job["id"], pid)
    check("s3.completed", st == "done", f"status={st}")


# ── Scenario 4: cancellation via real scancel ────────────────────────────────
def s4_cancellation():
    pid = projects.create_project("s4")["id"]; projects.set_current(pid)
    job = submit_python_job("import time; time.sleep(120)", "s4 cancel", None,
                            project_id=pid, estimate={"runtime_min": 5})
    info = _wait_running(job["id"], pid, timeout=60)
    check("s4.running_before_cancel", info.get("state") == "RUNNING", f"info={info}")
    ok = cancel_job(job["id"], project_id=pid)         # → scancel <id> + status cancelled
    check("s4.cancel_actionable", ok is True)
    time.sleep(5)
    j = get_job(job["id"], project_id=pid)
    check("s4.status_cancelled", j["status"] == "cancelled", f"status={j['status']}")
    # squeue should no longer list it (or show it completing/cancelled)
    post = _SUB.info(j) or {}
    check("s4.left_queue", post.get("state") in (None, "CANCELLED", "COMPLETING", "FAILED"),
          f"post-cancel squeue state={post.get('state')}")


# ── Scenario 5: error (non-zero exit) → failed + diagnostic captured ─────────
def s5_error():
    pid = projects.create_project("s5")["id"]; projects.set_current(pid)
    job = submit_python_job("raise ValueError('boom-on-the-node')", "s5 error", None,
                            project_id=pid, estimate={"runtime_min": 2})
    st = _drive_to_terminal(job["id"], pid)
    j = get_job(job["id"], project_id=pid)
    check("s5.status_failed", st == "failed", f"status={st}")
    blob = (j.get("error") or "") + (j.get("log_tail") or "")
    check("s5.error_captured", "boom-on-the-node" in blob or "ValueError" in blob,
          f"error={(j.get('error') or '')[:60]!r}")


# ── Scenario 6: the PRODUCTION poll loop finalizes a job autonomously ────────
def s6_poll_loop_autonomy():
    pid = projects.create_project("s6")["id"]; projects.set_current(pid)
    job = submit_python_job("print('via-poll-loop')", "s6 loop", None,
                            project_id=pid, estimate={"runtime_min": 2})

    async def _run():
        task = asyncio.ensure_future(_slurm_poll_loop())
        deadline = time.time() + 90
        while time.time() < deadline:
            if get_job(job["id"], project_id=pid)["status"] in ("done", "failed", "cancelled"):
                break
            await asyncio.sleep(2)
        task.cancel()
        try:
            await task
        except BaseException:  # noqa: BLE001
            pass
    _loop().run_until_complete(_run())
    st = get_job(job["id"], project_id=pid)["status"]
    check("s6.poll_loop_finalized", st == "done", f"status={st} (autonomous _slurm_poll_loop)")


# ── Scenario 7: session-allocation card reports the real Slurm allocation ────
def s7_session_allocation():
    from core.exec.hpc_session import session_allocation
    pid = projects.create_project("s7")["id"]; projects.set_current(pid)
    job = submit_python_job("import time; time.sleep(15)", "s7 sleeper", None,
                            project_id=pid, estimate={"runtime_min": 2})
    _wait_running(job["id"], pid, timeout=60)
    sid = get_job(job["id"], project_id=pid)["params"]["slurm_id"]
    os.environ["SLURM_JOB_ID"] = str(sid)
    try:
        sa = session_allocation()
    finally:
        os.environ.pop("SLURM_JOB_ID", None)
    check("s7.on_slurm", sa.get("on_slurm") is True, f"sa={sa}")
    check("s7.node_partition", bool(sa.get("node")) and sa.get("partition") == "normal",
          f"node={sa.get('node')} partition={sa.get('partition')}")
    _drive_to_terminal(job["id"], pid)


# ── Scenario 8: a running Slurm job survives an ABA restart (reconcile) ───────
def s8_restart_readoption():
    pid = projects.create_project("s8")["id"]; projects.set_current(pid)
    job = submit_python_job("import time; time.sleep(20); print('survivor')", "s8 survivor",
                            None, project_id=pid, estimate={"runtime_min": 2})
    _wait_running(job["id"], pid, timeout=60)
    update_job(job["id"], project_id=pid, status="running")
    reconcile_jobs()                       # simulate ABA restart sweep
    j = get_job(job["id"], project_id=pid)
    check("s8.survived_restart", j["status"] == "running",
          f"status={j['status']} (real Slurm job NOT reaped)")
    st = _drive_to_terminal(job["id"], pid)
    check("s8.readopted_to_done", st == "done", f"status={st} (poll loop re-adopted it)")


def main() -> int:
    for fn in (s1_submit_result, s2_resource_flags, s3_monitoring, s4_cancellation, s5_error,
               s6_poll_loop_autonomy, s7_session_allocation, s8_restart_readoption):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(fn.__name__ + ".exception", False, f"{type(e).__name__}: {e}")
    passed = sum(1 for _, c, _ in _RESULTS if c)
    print(f"\n=== {passed}/{len(_RESULTS)} checks passed ===", flush=True)
    return 0 if passed == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
