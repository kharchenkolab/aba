"""SlurmSubmitter (ondemand.md P6) — run a background job as a Slurm batch job.

ABA runs as a Slurm job on a compute node and submits FURTHER jobs via ``sbatch``
(nested submission assumed allowed). Completion is signaled by a sentinel file on
the shared filesystem — no callbacks/webhooks:

  job.sh:  python -m core.jobs.slurm_entry <spec>   (→ writes result.json)
           echo $? > <run_dir>/done                  (the completion sentinel)

The ABA-side poll loop (runner._slurm_poll_loop) watches ``done`` and finalizes
through the SHARED completion path (artifacts + continuation), falling back to
``sacct`` if the job died before writing the sentinel.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

from core.data.workspace import scratch_dir
from core.graph.jobs import update_job
from core.jobs.hpc_config import hpc_config, resolve_resources

_BACKEND_DIR = str(Path(__file__).resolve().parents[2])   # the dir containing core/

_SACCT_TERMINAL_FAIL = {"FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY",
                        "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED"}


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class SlurmSubmitter:
    name = "slurm"

    def _run_dir(self, job: dict) -> Path:
        pid = (job.get("params") or {}).get("project_id") or "default"
        return scratch_dir(str(pid), job["id"])

    # ── submit ───────────────────────────────────────────────────────────────
    def submit(self, job: dict) -> None:
        params = job.get("params") or {}
        pid = params.get("project_id") or "default"
        kind = job.get("kind") or "run_python"
        run_dir = self._run_dir(job)
        spec_path = run_dir / "job_spec.json"
        result_path = run_dir / "result.json"
        done_path = run_dir / "done"
        log_path, err_path = run_dir / "job.log", run_dir / "job.err"

        spec_path.write_text(json.dumps({
            "code": params.get("code", ""), "kind": kind, "project_id": str(pid),
            "run_id": job["id"], "timeout_s": int(params.get("timeout_s") or 600),
            "result_path": str(result_path),
        }))
        job_sh = run_dir / "job.sh"
        job_sh.write_text(
            "#!/bin/bash\n"
            f"cd {run_dir}\n"
            f"export PYTHONPATH={_BACKEND_DIR}:$PYTHONPATH\n"
            f"{sys.executable} -m core.jobs.slurm_entry {spec_path}\n"
            f"echo $? > {done_path}\n"
        )
        job_sh.chmod(0o755)

        res = resolve_resources(params.get("estimate") or {}, hpc_config())
        cmd = ["sbatch", "--parsable",
               f"--job-name=aba-{job['id']}",
               f"--output={log_path}", f"--error={err_path}", f"--chdir={run_dir}",
               f"--cpus-per-task={res['cores']}",
               f"--mem={res['mem_gb']}G",
               f"--time={res['walltime_h'] * 60}"]          # minutes
        if res.get("partition"):
            cmd.append(f"--partition={res['partition']}")
        if res.get("qos"):
            cmd.append(f"--qos={res['qos']}")
        if res.get("account"):
            cmd.append(f"--account={res['account']}")
        if res.get("gpu"):
            cmd.append("--gres=gpu:1")
        cmd.append(str(job_sh))

        proc = _run(cmd, timeout=60)
        slurm_id = (proc.stdout or "").strip().split(";")[0]   # --parsable → "<id>[;cluster]"
        if proc.returncode != 0 or not slurm_id:
            update_job(job["id"], project_id=pid, status="failed",
                       error=f"sbatch failed: {((proc.stderr or proc.stdout) or '')[-500:]}")
            return
        update_job(job["id"], project_id=pid,
                   params={**params, "slurm_id": slurm_id, "submitter": "slurm",
                           "run_dir": str(run_dir), "resources": res})

    # ── poll (the externally-run job's terminal result, else None) ───────────
    def poll(self, job: dict) -> Optional[dict]:
        params = job.get("params") or {}
        run_dir = Path(params.get("run_dir") or self._run_dir(job))
        done = run_dir / "done"
        if done.exists():
            try:
                rc = int((done.read_text().strip() or "1"))
            except ValueError:
                rc = 1
            rp = run_dir / "result.json"
            if rp.exists():
                try:
                    return json.loads(rp.read_text())
                except Exception:  # noqa: BLE001
                    pass
            return ({"returncode": 0, "stdout": "", "stderr": ""} if rc == 0
                    else {"error": f"slurm job exited {rc}", "returncode": rc})
        sid = params.get("slurm_id")
        if sid:
            st = self._sacct_state(sid)
            if st in _SACCT_TERMINAL_FAIL:
                return {"error": f"slurm job {st} (no result written)", "returncode": 1}
        return None

    def cancel(self, job: dict) -> None:
        sid = (job.get("params") or {}).get("slurm_id")
        if sid:
            _run(["scancel", str(sid)], timeout=15)

    # ── live info for the (i) monitor ────────────────────────────────────────
    def info(self, job: dict) -> dict:
        params = job.get("params") or {}
        sid = params.get("slurm_id")
        out: dict = {"submitter": "slurm", "slurm_id": sid, "resources": params.get("resources")}
        if not sid:
            return out
        sq = _run(["squeue", "-j", str(sid), "-h", "-o", "%T|%N|%M|%C|%m"], timeout=15)
        line = (sq.stdout or "").strip()
        if line:
            f = line.split("|")
            keys = ["state", "node", "elapsed", "cores", "mem"]
            out.update({k: (f[i] if i < len(f) and f[i] else None) for i, k in enumerate(keys)})
        else:
            out["state"] = self._sacct_state(sid)
        return out

    def _sacct_state(self, slurm_id: str) -> Optional[str]:
        p = _run(["sacct", "-j", str(slurm_id), "-n", "-P", "-o", "State"], timeout=20)
        for ln in (p.stdout or "").splitlines():
            tok = ln.strip().split()[0] if ln.strip() else ""
            if tok and tok != "State":
                return tok.upper().rstrip("+")     # "CANCELLED+" → "CANCELLED"
        return None
