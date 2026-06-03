"""Regression test for #334 follow-up: background-job env parity.

The interactive kernel (core/exec/kernels/jupyter.py _kernel_env) seeds
WORK_DIR / DATA_DIR / ARTIFACTS_DIR as REAL env vars. The shared stateless
core (core/exec/run.run_python_code) was missing this — a backgrounded
download via os.environ["WORK_DIR"] crashed with KeyError before doing any
work (live 2026-06-03 prj_413593e1 job_53df2f2734). This test pins the
contract: env vars must be visible to the agent's code in both lanes.

Run:
    .venv/bin/python tests/d19_run_python_env_parity.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from core.exec.run import run_python_code


def test_env_vars_visible_to_subprocess():
    code = (
        "import os\n"
        "print('WORK_DIR=' + os.environ.get('WORK_DIR', '<missing>'))\n"
        "print('DATA_DIR=' + os.environ.get('DATA_DIR', '<missing>'))\n"
        "print('ARTIFACTS_DIR=' + os.environ.get('ARTIFACTS_DIR', '<missing>'))\n"
        "print('MPLBACKEND=' + os.environ.get('MPLBACKEND', '<missing>'))\n"
    )
    result = run_python_code(code, project_id="_workspace", run_id="d19_env_probe",
                             timeout_s=30)
    assert result.get("returncode") == 0, result
    out = result.get("stdout", "")
    print("--- subprocess stdout ---")
    print(out)
    for key in ("WORK_DIR", "DATA_DIR", "ARTIFACTS_DIR", "MPLBACKEND"):
        line = next((ln for ln in out.splitlines() if ln.startswith(f"{key}=")), "")
        val = line.split("=", 1)[1] if "=" in line else ""
        assert val and val != "<missing>", f"{key} not seeded into subprocess env: {line!r}"
    # WORK_DIR should match the per-run scratch dir
    work_line = next((ln for ln in out.splitlines() if ln.startswith("WORK_DIR=")), "")
    assert "d19_env_probe" in work_line, f"WORK_DIR not the per-run scratch: {work_line!r}"
    print("OK env vars (WORK_DIR/DATA_DIR/ARTIFACTS_DIR/MPLBACKEND) visible to subprocess")


def test_agent_pattern_works():
    """The specific pattern that crashed the live session — directly."""
    code = (
        "import os\n"
        "out_dir = os.path.join(os.environ['WORK_DIR'], 'GSE192391_counts')\n"
        "os.makedirs(out_dir, exist_ok=True)\n"
        "print('made:', out_dir)\n"
        "assert os.path.isdir(out_dir)\n"
    )
    result = run_python_code(code, project_id="_workspace", run_id="d19_dir_pattern",
                             timeout_s=30)
    assert result.get("returncode") == 0, result
    assert "made:" in (result.get("stdout", "")), result
    print("OK os.environ['WORK_DIR'] + os.makedirs pattern works")


if __name__ == "__main__":
    test_env_vars_visible_to_subprocess()
    test_agent_pattern_works()
    print("\nALL OK")
