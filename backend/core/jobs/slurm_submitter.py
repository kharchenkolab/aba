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

        # Cluster module provider: the modules this project resolved (recorded by
        # ensure_capability) get `module load`ed in the login-less job script.
        # project_modules() filters python-toolchain modules (they'd shadow the conda
        # env) and is [] off-cluster, so this is a safe no-op there. The Python
        # interpreter env is sanitized AFTER the load (below) so no module — even one
        # the name filter misses — can shadow the conda env's Python (the prj_6d986f40
        # incident), while the module's PATH/LD_LIBRARY_PATH/JAVA_HOME/etc. for tools
        # are preserved.
        from core.exec.modules import load_lines, project_modules
        mods = list(params.get("modules") or [])
        for _m in project_modules(pid):
            if _m not in mods:
                mods.append(_m)
        # Nextflow head job: get `nextflow` on the compute-node head's PATH (run_nextflow_code then
        # finds it without conda). A self-installed shared-FS NF (ABA_NEXTFLOW_BIN) is PREPENDED to
        # PATH; otherwise `module load` the site module. bin-when-set / module-otherwise keeps fat
        # SIF + personal installs on their module path unchanged (misc/nfcore.md §7d).
        _nf_path_line = ""
        if kind == "run_nextflow":
            from core.exec.nextflow import nextflow_config, nextflow_bin_dir
            _nfcfg = nextflow_config()
            _nf_bin = nextflow_bin_dir(_nfcfg.get("bin"))
            if _nf_bin:
                _nf_path_line = f'export PATH="{_nf_bin}:$PATH"\n'
            elif _nfcfg.get("module") and _nfcfg["module"] not in mods:
                mods.append(_nfcfg["module"])
        spec_path.write_text(json.dumps({
            "code": params.get("code", ""), "kind": kind, "project_id": str(pid),
            # Run UNDER the Run captured at submit (active_run_id), not the job's
            # own id — so harvested artifacts land in the Run's work dir and
            # attach to it (no agent re-render). Job CONTROL files (spec/result/
            # done/job.log) stay in the job dir via _run_dir(). Falls back to the
            # job id when there was no open Run.
            "run_id": params.get("run_id") or job["id"],
            "timeout_s": int(params.get("timeout_s") or 600),
            "result_path": str(result_path), "env": params.get("env"),
            "modules": mods,                              # provenance (cluster module provider)
            # Nextflow passthrough (None for python/r jobs).
            "pipeline": params.get("pipeline"), "revision": params.get("revision"),
            "profile": params.get("profile"), "nf_params": params.get("nf_params"),
            "outdir": params.get("outdir"), "execution": params.get("execution"),
            "local_resources": params.get("local_resources"),
        }))
        job_sh = run_dir / "job.sh"
        job_sh.write_text(
            "#!/bin/bash\n"
            f"cd {run_dir}\n"
            f"{load_lines(mods)}"
            f"{_nf_path_line}"
            # Sanitize the Python interpreter env AFTER `module load`: a cluster module
            # can set PYTHONHOME or prepend its own (ancient) PYTHONPATH, which would
            # shadow the conda env this job runs on (the prj_6d986f40 numpy incident).
            # Clear PYTHONHOME and OVERWRITE PYTHONPATH with just the backend (dropping
            # any module-injected entries) — the env's python then uses its own stdlib +
            # site-packages. Module PATH/LD_LIBRARY_PATH/JAVA_HOME/… for tools stay.
            "unset PYTHONHOME\n"
            f"export PYTHONPATH={_BACKEND_DIR}\n"
            # -u: unbuffered stdout so slurm_entry's tee'd child output reaches
            # job.log (sbatch -o) live, not only at exit.
            f"{sys.executable} -u -m core.jobs.slurm_entry {spec_path}\n"
            f"echo $? > {done_path}\n"
        )
        job_sh.chmod(0o755)

        if kind == "run_nextflow":
            # Size the head allocation from the site's nextflow config — NOT the pipeline's
            # task estimate — through resolve_resources so the walltime maps to a valid
            # partition/QOS/account. In "slurm" mode the head is a lightweight orchestrator
            # (modest cores/mem) that fans heavy tasks out as their own jobs; in "local" mode
            # the head IS the worker (tasks run on its node), so use the bigger `local` block.
            from core.exec.nextflow import nextflow_config
            ncfg = nextflow_config()
            mode = (params.get("execution") or ncfg.get("execution") or "slurm").lower()
            blk = dict(ncfg["local"] if mode == "local" else ncfg["head"])
            # In local mode prefer the estimate-derived allocation (sized to the pipeline's
            # heaviest task) over the flat default; fall back to nextflow.local on a miss.
            lr = params.get("local_resources") if mode == "local" else None
            if lr:
                blk["cores"] = lr.get("cores") or blk.get("cores")
                blk["mem_gb"] = lr.get("mem_gb") or blk.get("mem_gb")
            head_est = {"runtime_min": int(blk.get("walltime_h") or 24) * 60,
                        "cores": blk.get("cores") or 2, "mem_gb": blk.get("mem_gb") or 8}
            res = resolve_resources(head_est, hpc_config())
            if blk.get("qos"):
                res["qos"] = blk["qos"]
            if blk.get("partition"):
                res["partition"] = blk["partition"]
        else:
            res = resolve_resources(params.get("estimate") or {}, hpc_config())
        cmd = ["sbatch", "--parsable",
               f"--job-name=aba-{job['id']}",
               f"--output={log_path}", f"--error={err_path}", f"--chdir={run_dir}",
               f"--cpus-per-task={res['cores']}",
               f"--time={res['walltime_h'] * 60}"]          # minutes
        if int(res.get("mem_gb") or 0) > 0:                 # 0 → let the scheduler default
            cmd.append(f"--mem={res['mem_gb']}G")
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
        result = self._result_from_sentinel(run_dir)
        if result is not None:                      # the sentinel is AUTHORITATIVE
            return result
        sid = params.get("slurm_id")
        if not sid:
            return None
        # No sentinel yet. The job is still going if squeue (the LIVE state) lists
        # it — do NOT consult sacct here: sacct can return a STALE/historical
        # record for a reused job id (a dev cluster whose counter reset), which
        # would wrongly fail a job that's about to run. Also grace the brief
        # submit→scheduler window so a not-yet-queued job isn't mistaken for dead.
        if self._in_squeue(sid) or self._too_young(job):
            return None
        # Gone from squeue and past the grace: re-check the sentinel (it may have
        # just landed on the shared FS), then treat an sacct FAIL as a real death.
        result = self._result_from_sentinel(run_dir)
        if result is not None:
            return result
        st = self._sacct_state(sid)
        if st in _SACCT_TERMINAL_FAIL:
            # The job was killed by Slurm (walltime/node-fail/preempt/cancel) and
            # never wrote a result. `slurm_terminal_fail` marks this as an
            # INFRASTRUCTURE death — distinct from a result.json that reports a
            # non-zero exit (a real pipeline error). Lets the runner auto-resume a
            # Nextflow head whose unpredictable lifetime outran its walltime.
            return {"error": f"slurm job {st} (no result written)", "returncode": 1,
                    "slurm_terminal_fail": st}
        return None

    def _result_from_sentinel(self, run_dir: Path) -> Optional[dict]:
        done = run_dir / "done"
        if not done.exists():
            return None
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
        # `done` is here but result.json isn't readable yet. slurm_entry ALWAYS
        # writes result.json BEFORE job.sh writes `done`, so on a clean exit
        # (rc==0) a missing/unparseable result.json is NFS visibility lag on the
        # polling (login) node — NOT a real absence. Returning the empty fallback
        # would silently drop the job's stdout AND every harvested artifact: the
        # agent would think a successful job produced nothing, and the Run would
        # stay empty (the live 2026-06-29 Seurat symptom). Re-poll instead until
        # it shows up; bound the wait on `done`'s mtime so a genuinely truncated
        # run (or pathological lag) still terminates with the fallback.
        if rc == 0 and not self._result_overdue(done):
            return None
        return ({"returncode": 0, "stdout": "", "stderr": ""} if rc == 0
                else {"error": f"slurm job exited {rc}", "returncode": rc})

    @staticmethod
    def _result_overdue(done: Path, grace_s: float = 90.0) -> bool:
        """True once `done` is older than grace_s — bounds how long poll() waits
        for a lagging result.json (NFS acdirmax is ~60s) before giving up."""
        import time
        try:
            return (time.time() - done.stat().st_mtime) > grace_s
        except OSError:
            return True

    def _in_squeue(self, slurm_id) -> bool:
        """True iff squeue still lists the job (pending/running/completing) — the
        live 'is it active' truth, immune to sacct's historical records."""
        p = _run(["squeue", "-j", str(slurm_id), "-h", "-o", "%T"], timeout=15)
        return bool((p.stdout or "").strip())

    @staticmethod
    def _too_young(job: dict, grace_s: float = 30.0) -> bool:
        """True while the job is within the post-submit settling window (it may
        not have hit squeue yet), so we don't prematurely trust sacct."""
        from datetime import datetime, timezone
        ts = job.get("created_at")
        if not ts:
            return False
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
            return age < grace_s
        except Exception:  # noqa: BLE001
            return False

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
        # P1: live Nextflow progress — the head's trace file is updated as tasks
        # change state, so the (i) monitor / poll loop can show "N/M processes" while
        # the pipeline runs (the head sbatch job just looks like one RUNNING job to Slurm).
        if job.get("kind") == "run_nextflow":
            try:
                from core.exec.nextflow import parse_trace_rows, trace_progress
                from core.config import project_work_dir
                rid = params.get("run_id") or job["id"]
                trace = (project_work_dir(str(params.get("project_id") or "default"))
                         / str(rid) / "nf_reports" / "trace.txt")
                prog = trace_progress(parse_trace_rows(trace))
                if prog.get("total"):
                    out["nextflow"] = prog
            except Exception:  # noqa: BLE001 — progress is best-effort
                pass
        return out

    def _sacct_state(self, slurm_id: str) -> Optional[str]:
        p = _run(["sacct", "-j", str(slurm_id), "-n", "-P", "-o", "State"], timeout=20)
        for ln in (p.stdout or "").splitlines():
            tok = ln.strip().split()[0] if ln.strip() else ""
            if tok and tok != "State":
                return tok.upper().rstrip("+")     # "CANCELLED+" → "CANCELLED"
        return None
