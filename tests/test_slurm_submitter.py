"""ondemand.md P6 — SlurmSubmitter, validated with FAKE sbatch/sacct/squeue/scancel.

No real cluster needed: the fake `sbatch` writes the shared-FS sentinel + result.json
(configurable via FAKE_* env vars), so we exercise submit → resource flags → poll →
cancel → info → the full poll-loop finalize, all in-process.

Run: .venv/bin/python -m pytest tests/test_slurm_submitter.py
"""
from __future__ import annotations
import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_slurm_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform

from core import projects                                    # noqa: E402
from core.graph.jobs import create_job, get_job              # noqa: E402

projects.init()


def _run_async(coro):
    """Run a coroutine with a valid current loop even if a prior async test
    closed the global one (py3.12 get_event_loop quirk)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

_FAKE_SBATCH = r'''#!/usr/bin/env python3
import sys, os, json
from pathlib import Path
args = sys.argv[1:]
af = os.environ.get("FAKE_ARGS_FILE")
if af: Path(af).write_text("\n".join(args))
rc = int(os.environ.get("FAKE_SBATCH_RC", "0"))
if rc != 0:
    sys.stderr.write("sbatch: simulated submit failure\n"); sys.exit(rc)
run_dir = next((a.split("=", 1)[1] for a in args if a.startswith("--chdir=")), ".")
if os.environ.get("FAKE_SBATCH_SENTINEL", "1") == "1":
    rrc = int(os.environ.get("FAKE_SBATCH_RESULT_RC", "0"))
    rd = Path(run_dir)
    if rrc == 0:
        (rd / "result.json").write_text(json.dumps(
            {"returncode": 0, "stdout": "hi from slurm\n", "stderr": "", "plots": [], "tables": []}))
    else:
        (rd / "result.json").write_text(json.dumps(
            {"returncode": rrc, "stdout": "", "stderr": "boom\n"}))
    (rd / "done").write_text(f"{rrc}\n")
print(os.environ.get("FAKE_SLURM_ID", "12345"))
'''

_FAKE_SQUEUE = r'''#!/usr/bin/env python3
import os
line = os.environ.get("FAKE_SQUEUE_LINE", "")
if line:
    print(line)
'''

_FAKE_SACCT = r'''#!/usr/bin/env python3
import os
s = os.environ.get("FAKE_SACCT_STATE", "")
if s:
    print(s)
'''

_FAKE_SCANCEL = r'''#!/usr/bin/env python3
import sys, os
from pathlib import Path
f = os.environ.get("FAKE_SCANCEL_FILE")
if f: Path(f).write_text(" ".join(sys.argv[1:]))
'''


@pytest.fixture()
def slurm(monkeypatch):
    """Fake slurm binaries on PATH + ABA_BATCH_SUBMITTER=slurm."""
    bindir = Path(tempfile.mkdtemp(prefix="aba_fakeslurm_"))
    for name, body in (("sbatch", _FAKE_SBATCH), ("squeue", _FAKE_SQUEUE),
                       ("sacct", _FAKE_SACCT), ("scancel", _FAKE_SCANCEL)):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("ABA_BATCH_SUBMITTER", "slurm")
    # clean per-test FAKE_* so one test can't leak config into another
    for k in list(os.environ):
        if k.startswith("FAKE_"):
            monkeypatch.delenv(k, raising=False)
    return bindir


def _mk_job(pid: str, code="print('x')", kind="run_python", estimate=None, env=None) -> dict:
    import uuid
    jid = f"job_{uuid.uuid4().hex[:10]}"
    return create_job(job_id=jid, kind=kind, title="t", focus_entity_id=None,
                      params={"code": code, "timeout_s": 60, "project_id": pid,
                              "estimate": estimate or {}, "env": env}, project_id=pid)


def test_submit_stores_slurm_id(slurm):
    from core.jobs.slurm_submitter import SlurmSubmitter
    pid = projects.create_project("slurm-submit")["id"]
    job = _mk_job(pid)
    SlurmSubmitter().submit(job)
    after = get_job(job["id"], project_id=pid)
    assert after["params"]["slurm_id"] == "12345"
    assert after["params"]["submitter"] == "slurm"
    assert "resources" in after["params"]


def test_submit_passes_resource_flags(slurm, monkeypatch):
    from core.jobs.slurm_submitter import SlurmSubmitter
    args_file = Path(tempfile.mktemp())
    monkeypatch.setenv("FAKE_ARGS_FILE", str(args_file))
    monkeypatch.setenv("ABA_HPC_CONFIG", str(_write_cfg()))
    pid = projects.create_project("slurm-flags")["id"]
    job = _mk_job(pid, estimate={"cores": 8, "mem_gb": 32, "runtime_min": 300})
    SlurmSubmitter().submit(job)
    args = args_file.read_text()
    assert "--cpus-per-task=8" in args
    assert "--mem=32G" in args
    assert "--time=300" in args            # 5h → 300 min
    assert "--partition=long" in args      # 8 cores/5h doesn't fit 'short'
    assert "--parsable" in args


def _write_cfg() -> Path:
    p = Path(tempfile.mktemp(suffix=".yaml"))
    p.write_text(
        "partitions:\n"
        "  - {name: short, max_cores: 4, max_mem_gb: 16, max_walltime_h: 2, gpu: false}\n"
        "  - {name: long, max_cores: 32, max_mem_gb: 256, max_walltime_h: 72, gpu: false}\n"
        "defaults: {cores: 1, mem_gb: 4, walltime_h: 4}\n")
    return p


def test_poll_pending_then_done(slurm, monkeypatch):
    from core.jobs.slurm_submitter import SlurmSubmitter
    monkeypatch.setenv("FAKE_SBATCH_SENTINEL", "0")     # submit WITHOUT writing sentinel
    pid = projects.create_project("slurm-poll")["id"]
    job = _mk_job(pid)
    sub = SlurmSubmitter()
    sub.submit(job)
    after = get_job(job["id"], project_id=pid)
    assert sub.poll(after) is None                       # still running
    # now the job "finishes" — write the sentinel + result
    run_dir = Path(after["params"]["run_dir"])
    (run_dir / "result.json").write_text(json.dumps({"returncode": 0, "stdout": "done\n", "stderr": ""}))
    (run_dir / "done").write_text("0\n")
    res = sub.poll(after)
    assert res is not None and res["returncode"] == 0 and "done" in res["stdout"]


def test_sbatch_failure_marks_job_failed(slurm, monkeypatch):
    from core.jobs.slurm_submitter import SlurmSubmitter
    monkeypatch.setenv("FAKE_SBATCH_RC", "1")
    pid = projects.create_project("slurm-fail")["id"]
    job = _mk_job(pid)
    SlurmSubmitter().submit(job)
    after = get_job(job["id"], project_id=pid)
    assert after["status"] == "failed"
    assert "sbatch failed" in (after["error"] or "")


def test_cancel_calls_scancel(slurm, monkeypatch):
    from core.jobs.slurm_submitter import SlurmSubmitter
    scancel_file = Path(tempfile.mktemp())
    monkeypatch.setenv("FAKE_SCANCEL_FILE", str(scancel_file))
    pid = projects.create_project("slurm-cancel")["id"]
    job = _mk_job(pid)
    sub = SlurmSubmitter()
    sub.submit(job)
    sub.cancel(get_job(job["id"], project_id=pid))
    assert scancel_file.exists() and "12345" in scancel_file.read_text()


def test_info_parses_squeue(slurm, monkeypatch):
    from core.jobs.slurm_submitter import SlurmSubmitter
    monkeypatch.setenv("FAKE_SQUEUE_LINE", "RUNNING|node07|3:21|8|16G")
    pid = projects.create_project("slurm-info")["id"]
    job = _mk_job(pid)
    sub = SlurmSubmitter()
    sub.submit(job)
    info = sub.info(get_job(job["id"], project_id=pid))
    assert info["state"] == "RUNNING" and info["node"] == "node07"
    assert info["cores"] == "8" and info["elapsed"] == "3:21"


def test_poll_loop_finalizes_to_done(slurm):
    """Full path: submit through the runner (factory → SlurmSubmitter) then run a
    poll-loop iteration → the job reaches 'done' via the SHARED _finalize_job."""
    from core.jobs.runner import submit_python_job, _active_slurm_jobs, _finalize_job
    from core.jobs.submitter import get_submitter
    pid = projects.create_project("slurm-loop")["id"]
    projects.set_current(pid)
    job = submit_python_job("print('hi')", "t", None, project_id=pid)
    actives = [j for j in _active_slurm_jobs() if j["id"] == job["id"]]
    assert actives, "submitted slurm job should appear in _active_slurm_jobs"
    j = actives[0]
    result = get_submitter().poll(j)
    assert result is not None
    _run_async(_finalize_job(j, result, j["project_id"], j["project_id"]))
    assert get_job(job["id"], project_id=pid)["status"] == "done"


def test_estimate_threads_into_resources(slurm, monkeypatch):
    """submit_python_job(estimate=...) → params → SlurmSubmitter → sbatch flags."""
    from core.jobs.runner import submit_python_job
    from core.jobs.slurm_submitter import SlurmSubmitter
    args_file = Path(tempfile.mktemp())
    monkeypatch.setenv("FAKE_ARGS_FILE", str(args_file))
    monkeypatch.setenv("FAKE_SBATCH_SENTINEL", "0")
    monkeypatch.setenv("ABA_HPC_CONFIG", str(_write_cfg()))
    pid = projects.create_project("slurm-estimate")["id"]
    projects.set_current(pid)
    job = submit_python_job("print(1)", "t", None, project_id=pid,
                            estimate={"cores": 8, "mem_gb": 32, "runtime_min": 300})
    assert get_job(job["id"], project_id=pid)["params"]["estimate"]["cores"] == 8
    args = args_file.read_text()
    assert "--cpus-per-task=8" in args and "--partition=long" in args


def test_session_allocation_local(monkeypatch):
    from core.exec.hpc_session import session_allocation
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.delenv("SLURM_JOBID", raising=False)
    monkeypatch.setenv("ABA_BATCH_SUBMITTER", "local")
    sa = session_allocation()
    assert sa["on_slurm"] is False and sa["cores"] > 0 and sa["submitter"] == "local"


def test_session_allocation_slurm(slurm, monkeypatch):
    from core.exec.hpc_session import session_allocation
    monkeypatch.setenv("SLURM_JOB_ID", "999")
    monkeypatch.setenv("FAKE_SQUEUE_LINE", "node09|2:00:00|short|4|8G|0:30")
    sa = session_allocation()
    assert sa["on_slurm"] and sa["slurm_job_id"] == "999"
    assert sa["node"] == "node09" and sa["time_left"] == "2:00:00" and sa["partition"] == "short"


def test_reconcile_spares_slurm_jobs():
    """A Slurm job runs on the cluster and survives an ABA restart; reconcile must
    NOT reap its running row (a local running row IS reaped as a zombie)."""
    from core.jobs.runner import reconcile_jobs
    from core.graph.jobs import create_job, update_job, get_job
    pid = projects.create_project("slurm-reconcile")["id"]
    create_job(job_id="job_slurmrun", kind="run_python", title="t", focus_entity_id=None,
               params={"code": "x", "project_id": pid, "submitter": "slurm", "slurm_id": "77"},
               project_id=pid)
    update_job("job_slurmrun", project_id=pid, status="running")
    create_job(job_id="job_localrun", kind="run_python", title="t", focus_entity_id=None,
               params={"code": "x", "project_id": pid}, project_id=pid)
    update_job("job_localrun", project_id=pid, status="running")
    reconcile_jobs()
    assert get_job("job_slurmrun", project_id=pid)["status"] == "running"   # spared
    assert get_job("job_localrun", project_id=pid)["status"] == "failed"    # reaped


def test_submit_omits_mem_when_zero(slurm, monkeypatch):
    """mem_gb:0 → NO --mem flag (let the scheduler default; some nodes reject even
    --mem=1G when RealMemory < 1024MB). Real dev-cluster lesson."""
    from core.jobs.slurm_submitter import SlurmSubmitter
    args_file = Path(tempfile.mktemp())
    monkeypatch.setenv("FAKE_ARGS_FILE", str(args_file))
    cfg = Path(tempfile.mktemp(suffix=".yaml"))
    cfg.write_text("partitions:\n"
                   "  - {name: normal, max_cores: 1, max_mem_gb: 0, max_walltime_h: 1, gpu: false}\n"
                   "defaults: {partition: normal, cores: 1, mem_gb: 0, walltime_h: 1}\n")
    monkeypatch.setenv("ABA_HPC_CONFIG", str(cfg))
    pid = projects.create_project("slurm-nomem")["id"]
    SlurmSubmitter().submit(_mk_job(pid))
    args = args_file.read_text()
    assert "--mem=" not in args, args
    assert "--cpus-per-task=1" in args and "--partition=normal" in args


def test_submit_spec_carries_env(slurm):
    """The isolated-env name reaches the compute node via the job spec, so
    slurm_entry can activate it (run_python(env=…, background=True))."""
    from core.jobs.slurm_submitter import SlurmSubmitter
    pid = projects.create_project("slurm-env")["id"]
    job = _mk_job(pid, env="myenv")
    SlurmSubmitter().submit(job)
    rd = get_job(job["id"], project_id=pid)["params"]["run_dir"]
    spec = json.loads((Path(rd) / "job_spec.json").read_text())
    assert spec["env"] == "myenv"
