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
    fn = run_r_code if (spec.get("kind") == "run_r") else run_python_code
    result = fn(spec["code"], project_id=spec["project_id"], run_id=spec["run_id"],
                timeout_s=int(spec.get("timeout_s") or 600))
    with open(spec["result_path"], "w") as f:
        json.dump(result, f, default=str)
    rc = result.get("returncode")
    if "error" in result or (rc is not None and rc != 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
