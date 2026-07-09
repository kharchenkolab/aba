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
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional

from core.data.workspace import scratch_dir
from core.graph.jobs import update_job
from core.jobs.hpc_config import hpc_config, resolve_resources

_BACKEND_DIR = str(Path(__file__).resolve().parents[2])   # the dir containing core/


def _offload_runtime() -> tuple[str, str]:
    """The Python interpreter + backend dir to bake into the offloaded job.sh.

    The job runs on a BARE compute node, so both paths must exist there. Native /
    linux-personal deploys run ABA directly on the host → ``sys.executable`` and
    ``_BACKEND_DIR`` are already real shared-FS paths (unchanged, the default).

    A SIF deploy runs ABA *inside a container* where those are container-internal
    (``/opt/aba-venv/bin/python`` + ``/opt/aba/backend``) and absent on the bare
    node. The launch sets ``ABA_OFFLOAD_PYTHON`` (the mounted base venv's real
    path) + ``ABA_OFFLOAD_BACKEND_DIR`` (a shared-FS copy of the backend, staged
    version-locked to the image) so offloaded jobs run bare — exactly like the
    native path — with no per-job container (see misc/slim_sif_deploy.md)."""
    py = os.environ.get("ABA_OFFLOAD_PYTHON") or sys.executable
    bd = os.environ.get("ABA_OFFLOAD_BACKEND_DIR") or _BACKEND_DIR
    return py, bd


def _job_wrap_mode() -> str:
    """``'sif'`` → offloaded env-jobs RE-ENTER the image via ``apptainer exec`` (a
    FAT deployment, where the conda/R env + interpreter + backend live ONLY inside
    the SIF, so a bare node can't reach them); ``''`` → run BARE on the node
    (native / slim — the default, unchanged). Set by the OOD launch
    (``ABA_JOB_WRAP=sif``) for a fat SIF. See misc/fatagain.md."""
    return (os.environ.get("ABA_JOB_WRAP") or "").strip().lower()


def _apptainer_tmpdir() -> str:
    """Node-local tmp/cache for a wrapped job's apptainer. MUST be off NFS home —
    apptainer hangs unkillably when its cache/tmp sit there (reproduced on CBE clip
    nodes; see core/exec/nextflow.py). Overridable via ABA_APPTAINER_TMPDIR."""
    return (os.environ.get("ABA_APPTAINER_TMPDIR")
            or f"/tmp/aba-apptainer-{os.environ.get('USER') or os.environ.get('LOGNAME') or 'u'}")


def _wrap_binds(run_dir: Path) -> list[str]:
    """Identity binds a wrapped job needs visible inside the SIF: its run dir + the
    per-user scope roots (runtime/envs/share) + the cluster shares, plus the
    cluster-module tool trees (ABA_MODULE_BINDS) so a module-resolved binary is
    reachable inside. Bound X→X so absolute paths match the bare node (the
    environment-equivalence contract — misc/fatagain.md)."""
    cands = [str(run_dir)]
    for var in ("ABA_RUNTIME_DIR", "ABA_ENVS_DIR", "ABA_SHARE"):
        v = os.environ.get(var)
        if v:
            cands.append(v)
    for p in ("/groups", "/cluster/aba", "/resources"):
        if os.path.isdir(p):
            cands.append(p)
    for m in (os.environ.get("ABA_MODULE_BINDS") or "").split():
        if m and os.path.exists(m):
            cands.append(m)
    seen: set = set()
    out: list = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _job_body(*, kind: str, mods: list, nf_path_line: str, spec_path: Path,
              run_dir: Path, done_path: Path, gpu: bool,
              job_py: str, job_backend: str) -> str:
    """Render the offloaded job.sh body.

    BARE (default — native / slim): the offloaded interpreter runs slurm_entry
    directly on the compute node against a shared-FS base + backend.

    WRAPPED (``ABA_JOB_WRAP=sif`` AND an env-job): re-enter the fat SIF via
    ``apptainer exec`` — under fat the interpreter/libs/backend exist ONLY in the
    image, so a bare env-job would find no interpreter (misc/fatagain.md, the "fat
    rule"). ``module load`` still runs on the HOST here: apptainer passes the host
    env through and the module tool trees are bound in (``_wrap_binds``), so a
    module-resolved binary resolves inside; PYTHONPATH is forced to the in-image
    backend and PYTHONHOME unset so a module can't shadow the conda python
    (prj_6d986f40). ``run_nextflow`` is NEVER wrapped (its head is handled
    separately) — it falls through to the bare branch."""
    from core.exec.modules import load_lines
    head = f"#!/bin/bash\ncd {run_dir}\n{load_lines(mods)}{nf_path_line}"
    sif = os.environ.get("ABA_SIF")
    if _job_wrap_mode() == "sif" and kind in ("run_python", "run_r") and sif:
        tmp = _apptainer_tmpdir()
        binds = " ".join(f"--bind {shlex.quote(p)}" for p in _wrap_binds(run_dir))
        nv = "--nv " if gpu else ""
        return (
            f"{head}"
            f"export APPTAINER_TMPDIR={shlex.quote(tmp)} APPTAINER_CACHEDIR={shlex.quote(tmp)}\n"
            f"mkdir -p {shlex.quote(tmp)}\n"
            "unset PYTHONHOME\n"
            # -u: unbuffered so slurm_entry's tee'd child output reaches job.log live.
            f"apptainer exec {nv}{binds} --env PYTHONPATH=/opt/aba/backend "
            f"{shlex.quote(sif)} /opt/aba-venv/bin/python -u -m core.jobs.slurm_entry {spec_path}\n"
            f"echo $? > {done_path}\n"
        )
    # BARE (native / slim) — unchanged. Sanitize the interpreter env AFTER `module
    # load`: a cluster module can set PYTHONHOME or prepend its own (ancient)
    # PYTHONPATH, shadowing the conda env (prj_6d986f40). Clear PYTHONHOME + OVERWRITE
    # PYTHONPATH with just the backend; module PATH/LD_LIBRARY_PATH/JAVA_HOME stay.
    return (
        f"{head}"
        "unset PYTHONHOME\n"
        f"export PYTHONPATH={job_backend}\n"
        f"{job_py} -u -m core.jobs.slurm_entry {spec_path}\n"
        f"echo $? > {done_path}\n"
    )


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
        from core.exec.modules import project_modules   # load_lines used in _job_body
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
            # Did the agent request a GPU? slurm_entry uses this to preflight torch.cuda
            # on the compute node — so a GPU job can't silently fall to CPU on an idle
            # allocated GPU (the scVI-on-CPU incident).
            "gpu": bool((params.get("estimate") or {}).get("gpu")),
            "modules": mods,                              # provenance (cluster module provider)
            # Nextflow passthrough (None for python/r jobs).
            "pipeline": params.get("pipeline"), "revision": params.get("revision"),
            "profile": params.get("profile"), "nf_params": params.get("nf_params"),
            "outdir": params.get("outdir"), "execution": params.get("execution"),
            "local_resources": params.get("local_resources"),
        }))
        job_sh = run_dir / "job.sh"
        if kind == "run_nextflow":
            # nf-core head is a BARE bash job: `module load nextflow; nextflow run …`
            # with no python/backend on the node (misc/fatagain.md). The ABA server
            # harvests the run dir afterward (poll → harvest_nextflow_result), so
            # parsing never runs on the node. Works identically under fat and slim.
            from core.exec.nextflow import nextflow_head_script
            body, nf_ctx = nextflow_head_script(
                pipeline=params.get("pipeline") or "", project_id=str(pid),
                run_id=params.get("run_id") or job["id"],
                revision=params.get("revision"), profile=params.get("profile"),
                params=params.get("nf_params") or {}, outdir=params.get("outdir"),
                execution=params.get("execution"), local_resources=params.get("local_resources"),
                done_path=str(done_path))   # completion sentinel in the JOB dir (poll watches it)
            job_sh.write_text(body)
            params = {**params, "nf_harvest": nf_ctx}   # poll() harvests from these paths
        else:
            job_py, job_backend = _offload_runtime()   # bare-node interpreter + backend dir
            job_sh.write_text(_job_body(
                kind=kind, mods=mods, nf_path_line=_nf_path_line, spec_path=spec_path,
                run_dir=run_dir, done_path=done_path,
                gpu=bool((params.get("estimate") or {}).get("gpu")),
                job_py=job_py, job_backend=job_backend))
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
        # A decoupled Nextflow head ran BARE bash (no python/backend, no result.json)
        # — harvest its run dir SERVER-SIDE once `done` lands. Other jobs read the
        # result.json the in-node slurm_entry wrote (the sentinel is AUTHORITATIVE).
        nf_ctx = params.get("nf_harvest") if job.get("kind") == "run_nextflow" else None
        if nf_ctx is not None:
            if done.exists():
                return self._harvest_nextflow(nf_ctx, run_dir, params)
        else:
            result = self._result_from_sentinel(run_dir)
            if result is not None:
                return result
        sid = params.get("slurm_id")
        if not sid:
            return None
        # Not done yet. The job is still going if squeue (the LIVE state) lists it — do
        # NOT consult sacct here: sacct can return a STALE/historical record for a
        # reused job id (a dev cluster whose counter reset), which would wrongly fail a
        # job that's about to run. Also grace the brief submit→scheduler window.
        if self._in_squeue(sid) or self._too_young(job):
            return None
        # Gone from squeue and past the grace: re-check completion (it may have just
        # landed on the shared FS), then treat an sacct FAIL as a real death.
        if nf_ctx is not None:
            if done.exists():
                return self._harvest_nextflow(nf_ctx, run_dir, params)
        else:
            result = self._result_from_sentinel(run_dir)
            if result is not None:
                return result
        st = self._sacct_state(sid)
        if st in _SACCT_TERMINAL_FAIL:
            # The job was killed by Slurm (walltime/node-fail/preempt/cancel) and never
            # wrote `done`. `slurm_terminal_fail` marks this an INFRASTRUCTURE death —
            # distinct from a completed run whose result reports a non-zero exit (a real
            # pipeline error). Lets the runner auto-resume a Nextflow head whose
            # unpredictable lifetime outran its walltime.
            return {"error": f"slurm job {st} (no result written)", "returncode": 1,
                    "slurm_terminal_fail": st}
        return None

    def _harvest_nextflow(self, ctx: dict, run_dir: Path, params: dict) -> dict:
        """Build the decoupled Nextflow head's result from its completed run dir. The
        head ran `nextflow` bare, so ALL parsing/harvest (trace/MultiQC/failure/
        artifacts) happens SERVER-SIDE here (misc/fatagain.md). stdout/stderr are the
        head's sbatch-captured job.log/job.err; rc is the `done` sentinel."""
        from core.exec.nextflow import harvest_nextflow_result
        done = run_dir / "done"
        try:
            rc = int(((done.read_text().strip() if done.exists() else "") or "1"))
        except ValueError:
            rc = 1
        nf_ver = ""
        try:
            import re as _re
            vtxt = (Path(ctx["scratch"]) / "nf_version.txt").read_text()
            m = _re.search(r"version\s+([0-9][0-9.]*)", vtxt)
            nf_ver = m.group(1) if m else ""
        except Exception:  # noqa: BLE001
            pass
        return harvest_nextflow_result(
            scratch=ctx["scratch"], outdir=ctx["outdir"], reports=ctx["reports"],
            returncode=rc, stdout=self._read_tail(run_dir / "job.log"),
            stderr=self._read_tail(run_dir / "job.err"),
            pipeline=ctx.get("pipeline") or "", revision=ctx.get("revision"),
            profile=ctx.get("profile"), params=ctx.get("params") or {},
            project_id=ctx.get("project_id") or str(params.get("project_id") or "default"),
            run_id=ctx.get("run_id") or "", command=ctx.get("command") or "", nf_version=nf_ver)

    @staticmethod
    def _read_tail(p: Path, max_bytes: int = 200_000) -> str:
        try:
            return p.read_bytes()[-max_bytes:].decode("utf-8", "replace")
        except OSError:
            return ""

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
