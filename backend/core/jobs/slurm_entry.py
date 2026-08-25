"""core.jobs.slurm_entry — runs ON the compute node, inside the Slurm job.sh.

Invoked as ``python -m core.jobs.slurm_entry <job_spec.json>``. It runs the code
through the SAME execution core ABA uses synchronously (run_python_code /
run_r_code), so artifacts harvest to the shared content-addressed store
identically; then it dumps the result_obj to ``result.json``. The wrapper job.sh
writes the ``done`` sentinel (with this process' exit code) afterward, which the
ABA-side poll loop watches.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _interp_from_activation(spec: dict) -> str | None:
    """The interpreter for a run_python/run_r job. A modern weft job carries NO
    aba-resolved `interp` (raw prefix paths break under the squashfs realization
    strategy); instead the task ran with `env=<EnvID>` and weft ACTIVATED it, so
    the mounted prefix is live in `$CONDA_PREFIX` and its `bin/` is first on PATH.
    Read it here — strategy-blind (works for squashfs AND directory-prefix envs).
    Falls back to a spec-carried `interp` (legacy path / explicit override)."""
    if spec.get("interp"):
        return spec["interp"]
    # WEFT_PREFIX is the substrate's OWN statement that activation took; it is
    # exported by the activation guard in cmd.sh only after CONDA_PREFIX has
    # been checked to be real, and the guard exits 78 before user code
    # otherwise. CONDA_PREFIX is an inference over pixi shell-hook fallout that
    # anything in the chain can clobber, and it cannot distinguish "activation
    # failed" from "submitted deliberately bare". Prefer the fact; keep the
    # inference as the fallback for substrates predating the guard.
    prefix = os.environ.get("WEFT_PREFIX") or os.environ.get("CONDA_PREFIX")
    if not prefix:
        # None here is NOT self-explanatory: run.py's default lane asks for a
        # compute substrate, and this process has none by design. _activation_
        # verdict() is what turns that into an honest message — see there.
        return None
    exe = "Rscript" if spec.get("kind") == "run_r" else "python"
    return str(Path(prefix) / "bin" / exe)


def _activation_verdict(spec: dict) -> str | None:
    """Why this job cannot honestly run here — or None to proceed.

    slurm_entry is started as ``python -m``, so the FastAPI lifespan never
    runs and this process has NO compute substrate: that is deliberate, the
    node runs only what the scheduler mounted for the task. The consequence
    used to be silent and misleading. A job whose env was never activated
    resolved no interpreter, run.py fell into its default lane, asked for the
    substrate that was never going to be here, and the step failed with
    ``substrate_offline: compute substrate not configured yet`` — which reads
    as the cluster being broken. Foreground work kept running the whole time
    (it rides the live session, not a node), so the report that reached us
    was "background jobs fail instantly, no output" (field report, 2026-08).

    Three cases, and only the middle one is a failure:
      * no ``env_id`` — deliberately bare (the ``env='system'`` lever): run
        on the node's own interpreter, as asked;
      * ``env_id`` set but nothing activated — an ACTIVATION failure, named
        as one here rather than discovered as a missing substrate;
      * ``env_id`` set and a DIFFERENT env activated — refuse; running the
        job in the wrong environment silently is the worse outcome.
    """
    want = spec.get("env_id")
    if not want or spec.get("interp"):
        return None                        # bare by design, or explicit override
    if not (os.environ.get("WEFT_PREFIX") or os.environ.get("CONDA_PREFIX")):
        return (f"environment {want} was never activated on this node — "
                f"neither WEFT_PREFIX nor CONDA_PREFIX is set, so there is no "
                f"interpreter to run the job in. (A current substrate refuses "
                f"earlier than this: its activation guard exits 78 from cmd.sh "
                f"before user code, classified env.activation_failed. Reaching "
                f"here means the guard did not run — an older substrate, or a "
                f"task submitted with no env.) This is an environment-"
                f"activation failure, not "
                f"a fault in the job's code and not a cluster outage: this "
                f"node runs only what the scheduler mounts for the task. "
                f"Check the job's task record for an activation error, then "
                f"re-submit.")
    seen = os.environ.get("WEFT_ENV_ID")
    if seen and seen != want:
        return (f"the job asked for environment {want} but {seen} was "
                f"activated on this node — refusing to run in the wrong "
                f"environment rather than reporting results from it.")
    return None


def main() -> int:
    with open(sys.argv[1]) as f:
        spec = json.load(f)
    # Before anything is dispatched: can this node honestly run the job it
    # was given? Same shape as the GPU preflight and the numpy canary below.
    _verdict = _activation_verdict(spec)
    if _verdict:
        with open(spec["result_path"], "w") as f:
            json.dump({"error": _verdict, "returncode": 1}, f, default=str)
        return 1
    from core.exec.run import run_python_code, run_r_code
    # stream=True tees the child's stdout/stderr to THIS process's stdout, which
    # sbatch captures to job.log (-o) — so the running job is tailable live
    # rather than silent until result.json is written at the end.
    kw = dict(project_id=spec["project_id"], run_id=spec["run_id"],
              timeout_s=int(spec.get("timeout_s") or 600), stream=True,
              interp=_interp_from_activation(spec))
    kind = spec.get("kind")
    if kind == "run_nextflow":               # the Nextflow HEAD process; fans tasks out via the site executor
        from core.exec.nextflow import run_nextflow_code
        result = run_nextflow_code(
            spec.get("pipeline") or "", project_id=spec["project_id"], run_id=spec["run_id"],
            revision=spec.get("revision"), profile=spec.get("profile"),
            params=spec.get("nf_params") or {}, outdir=spec.get("outdir"),
            execution=spec.get("execution"), local_resources=spec.get("local_resources"),
            timeout_s=int(spec.get("timeout_s") or 3600), stream=True)
    elif kind == "run_r":                    # isolated R env = its lib first on .libPaths()
        result = run_r_code(spec["code"], env=spec.get("env"), **kw)
    else:                                    # isolated python env = its own python, standalone
        # GPU preflight (verify-at-use): a job that REQUESTED a GPU must land on a
        # working CUDA torch — else it silently trains on CPU on an idle allocated GPU
        # (the scVI-on-CPU incident: correct placement, CPU-only torch base). Abort
        # LOUDLY + actionably here instead of burning the allocation. torch absent →
        # a non-torch GPU job, so we don't judge (ok is None). Applies in base AND
        # isolated envs — a GPU job must be able to use the GPU either way.
        if spec.get("gpu"):
            from core.exec.verify import gpu_capability_ok
            _gpu_ok, _gpu_detail = gpu_capability_ok()
            if _gpu_ok is False:
                result = {"error": "GPU requested but no usable GPU is visible to torch on "
                                   "this compute node — the job would run on CPU on an idle "
                                   "allocated GPU. Likely a CPU-only torch base; see "
                                   "docs/arch/envs.md (ABA_ACCELERATOR / deployment-conditional "
                                   "base). Detail: " + _gpu_detail, "returncode": 1}
                with open(spec["result_path"], "w") as f:
                    json.dump(result, f, default=str)
                return 1
        # Env canary (prj_6d986f40): a base-env python job must have a working numpy
        # (it ships in the base scientific stack). If `import numpy` fails, a loaded
        # cluster module has shadowed the conda env — fail LOUDLY + actionably here
        # rather than with a cryptic ImportError deep in the user's code. Isolated
        # envs (spec.env set) are self-contained, so skip the canary there.
        if not spec.get("env"):
            from core.exec.verify import verify_python_imports
            _ok, _detail = verify_python_imports(["numpy"])
            if not _ok:
                result = {"error": "background-job Python environment is broken: `import numpy` "
                                   "failed — a loaded cluster module likely shadows the conda env "
                                   "(check the project's modules / job.sh). Detail: " + _detail}
                with open(spec["result_path"], "w") as f:
                    json.dump(result, f, default=str)
                return 1
        result = run_python_code(spec["code"], env=spec.get("env"), **kw)
    if spec.get("env_id") and isinstance(result, dict):
        result["env_id"] = spec["env_id"]      # frozen identity → exec record
    with open(spec["result_path"], "w") as f:
        json.dump(result, f, default=str)
    rc = result.get("returncode")
    if "error" in result or (rc is not None and rc != 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
