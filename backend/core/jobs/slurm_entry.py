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
import sys


def main() -> int:
    with open(sys.argv[1]) as f:
        spec = json.load(f)
    from core.exec.run import run_python_code, run_r_code
    # stream=True tees the child's stdout/stderr to THIS process's stdout, which
    # sbatch captures to job.log (-o) — so the running job is tailable live
    # rather than silent until result.json is written at the end.
    kw = dict(project_id=spec["project_id"], run_id=spec["run_id"],
              timeout_s=int(spec.get("timeout_s") or 600), stream=True)
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
        result = run_python_code(spec["code"], env=spec.get("env"), **kw)
    with open(spec["result_path"], "w") as f:
        json.dump(result, f, default=str)
    rc = result.get("returncode")
    if "error" in result or (rc is not None and rc != 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
