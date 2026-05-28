"""
K2: persistent R (IRkernel) session — state persistence across run_r calls,
Python<->R file handoff on the shared thread dir, and restart. Real R kernel; the
first run installs r-irkernel via conda (slow). Uses the persistent ENVS_DIR so
reruns reuse the R install.

Run:
    .venv/bin/python tests/k2_r_kernel.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_k2_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "k2.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ.setdefault("ABA_ENVS_DIR", "/tmp/aba_e2e_envs")   # reuse cached R install
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import run_r, run_python, restart_kernel_tool  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    T = {"thread_id": "RT"}

    print("R session persistence (installs r-irkernel on first run — slow)")
    r1 = run_r({"code": "x <- 20 + 22\ncat('x is', x, '\\n')"}, T)
    check("first R cell runs", r1.get("returncode") == 0 and "x is 42" in (r1.get("stdout") or ""), str(r1)[:200])
    r2 = run_r({"code": "cat('reuse', x * 2, '\\n')"}, T)
    check("R state persists across run_r calls", r2.get("returncode") == 0 and "reuse 84" in (r2.get("stdout") or ""), str(r2)[:200])
    rerr = run_r({"code": "stop('boom')"}, T)
    check("R error captured", rerr.get("returncode") != 0 and "boom" in (rerr.get("stderr") or ""), str(rerr)[:200])

    print("Python -> R handoff via the shared thread directory")
    run_python({"code": "import pandas as pd\npd.DataFrame({'g':['A','B','C'],'v':[10,20,30]}).to_csv('handoff.csv', index=False)\nprint('wrote handoff.csv')"}, T)
    rh = run_r({"code": "d <- read.csv('handoff.csv')\ncat('rows', nrow(d), 'sum', sum(d$v), '\\n')"}, T)
    check("R reads the CSV written by Python (shared cwd)",
          rh.get("returncode") == 0 and "rows 3 sum 60" in (rh.get("stdout") or ""), str(rh)[:200])

    print("restart clears the R session")
    restart_kernel_tool({}, T)
    r3 = run_r({"code": "cat('x exists:', exists('x'), '\\n')"}, T)
    check("after restart, R state is gone", "x exists: FALSE" in (r3.get("stdout") or ""), str(r3)[:200])

    from core.exec.kernels import get_pool
    get_pool().shutdown_all()

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL K2 R-KERNEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
