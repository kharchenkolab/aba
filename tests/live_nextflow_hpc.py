"""LIVE Slurm smoke test for P0 HPC-routed Nextflow.

Submits run_nextflow as a background job through the REAL SlurmSubmitter: the
Nextflow HEAD runs as an sbatch job on a compute node; we poll it to terminal via
the same SlurmSubmitter.poll() the backend uses, then run the shared _finalize_job
(exec record + artifact harvest + Run attach). Proves the P0 plumbing end-to-end.

  Tier 1 (default): `nextflow-io/hello` (executor=local on the head) — fast, no
          containers; validates submit → head Slurm job → nextflow runs → harvest
          → finalize → job done.
  Tier 2 (--rnaseq): `nf-core/rnaseq -profile test,cbe,singularity` — exercises the
          cbe slurm executor (nested sbatch), singularity from the shared cache,
          igenomes, and MultiQC harvest. Slow (container pulls + many tasks).

Run from the login node (needs sbatch):
  ABA_RUNTIME_DIR=/groups/.../.nf_live_rt \
    ~/data/aba/install/env/bin/python tests/live_nextflow_hpc.py [--rnaseq]
"""
from __future__ import annotations
import asyncio
import os
import sys
import time
from pathlib import Path

os.environ["ABA_BATCH_SUBMITTER"] = "slurm"
os.environ.setdefault("ABA_NEXTFLOW_MODULE", "nextflow/24.10.6")
os.environ.setdefault("ABA_NEXTFLOW_WORKDIR", f"/scratch-cbe/users/{os.environ.get('USER','x')}/nxf")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

RNASEQ = "--rnaseq" in sys.argv


def main() -> int:
    from core import projects
    from core.graph.jobs import get_job
    from core.jobs.runner import submit_nextflow_job, _finalize_job
    from core.jobs.submitter import get_submitter

    projects.init()
    pid = projects.create_project("nf-live")["id"]
    projects.set_current(pid)

    if RNASEQ:
        os.environ["ABA_NEXTFLOW_PROFILES"] = "cbe,singularity"
        os.environ["ABA_NEXTFLOW_CACHEDIR"] = "/resources/containers"
        pipeline, revision, profile, budget = "nf-core/rnaseq", "3.14.0", "test", 60 * 40
    else:
        pipeline, revision, profile, budget = "nextflow-io/hello", None, None, 60 * 12

    print(f"[submit] {pipeline} (profile={profile}, rev={revision})")
    job = submit_nextflow_job(pipeline=pipeline, title=f"live {pipeline}",
                              focus_entity_id=None, revision=revision, profile=profile,
                              nf_params={}, project_id=pid, run_id="nf-live-run",
                              timeout_s=budget)
    jid = job["id"]
    sub = get_submitter()
    print(f"[submit] job={jid} submitter={sub.name}")

    # poll like the backend's _slurm_poll_loop
    deadline = time.time() + budget
    result = None
    last = ""
    while time.time() < deadline:
        job = get_job(jid, project_id=pid)
        params = job.get("params") or {}
        sid = params.get("slurm_id")
        if not sid and params.get("submitter") != "slurm":
            time.sleep(2); continue
        info = sub.info(job)
        state = info.get("state") or "?"
        prog = info.get("nextflow") or {}
        snap = f"{state}|{prog.get('completed')}/{prog.get('total')}"
        if snap != last:
            print(f"[poll] slurm_id={sid} state={state} node={info.get('node')} "
                  f"elapsed={info.get('elapsed')} nf_progress={prog or '-'}")
            last = snap
        result = sub.poll(job)
        if result is not None:
            break
        time.sleep(10)

    if result is None:
        print("[FAIL] timed out waiting for the head job")
        return 1

    rc = result.get("returncode")
    print(f"\n[result] returncode={rc} error={result.get('error')}")
    print("[result] stdout tail:\n  " + "\n  ".join((result.get("stdout") or "").splitlines()[-12:]))
    wf = result.get("workflow") or {}
    print(f"[result] workflow.engine={wf.get('engine')} images={len(wf.get('per_process_images') or [])} "
          f"outputs={len(result.get('outputs') or [])}")
    print(f"[result] harvested: plots={len(result.get('plots') or [])} "
          f"tables={len(result.get('tables') or [])} files={len(result.get('files') or [])}")
    print(f"[result] task_summary={result.get('task_summary')}")
    if wf.get("failure"):
        print(f"[result] failure={wf.get('failure')}")

    # the shared completion path: exec record (kind:workflow) + artifact registration
    asyncio.run(_finalize_job(job, result, pid, pid))
    final = get_job(jid, project_id=pid)
    print(f"[finalize] job status={final.get('status')} exec_id={result.get('exec_id')}")

    ok = (rc == 0 and final.get("status") == "done" and result.get("exec_id"))
    print("\n[PASS] P0 plumbing OK" if ok else "\n[FAIL] see above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
