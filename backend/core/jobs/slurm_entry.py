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
    prefix = os.environ.get("CONDA_PREFIX")
    if not prefix:
        return None                       # no activation → run.py raises loudly
    exe = "Rscript" if spec.get("kind") == "run_r" else "python"
    return str(Path(prefix) / "bin" / exe)


def main() -> int:
    with open(sys.argv[1]) as f:
        spec = json.load(f)
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
