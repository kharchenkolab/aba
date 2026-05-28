"""
K1 integration: run_python uses a persistent per-thread kernel by default —
state persists across calls; fresh=true is isolated; restart_kernel clears;
cross-thread isolated; artifact harvest collects only new files. Real kernels,
no model.

Run:
    .venv/bin/python tests/k1_run_python.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_k1rp_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "k1.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import run_python, restart_kernel_tool  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    T1 = {"thread_id": "T1"}
    T2 = {"thread_id": "T2"}

    print("persistence across run_python calls (one thread)")
    r1 = run_python({"code": "import pandas as pd\ndf = pd.DataFrame({'a':[1,2,3]})\nprint('made', len(df))"}, T1)
    check("first call runs in kernel", r1.get("returncode") == 0 and "made 3" in (r1.get("stdout") or ""), str(r1)[:200])
    r2 = run_python({"code": "print('reuse', int(df['a'].sum()))"}, T1)
    check("state persists across calls (df reused, no reload)",
          r2.get("returncode") == 0 and "reuse 6" in (r2.get("stdout") or ""), str(r2)[:200])

    print("fresh=true is isolated (no session state)")
    rf = run_python({"code": "print(df)", "fresh": True}, T1)
    check("fresh run has no session state → NameError",
          rf.get("returncode") != 0 and "NameError" in (rf.get("stderr") or ""), str(rf)[:200])

    print("cross-thread isolation")
    rx = run_python({"code": "print(df)"}, T2)
    check("other thread has no shared state → NameError",
          rx.get("returncode") != 0 and "NameError" in (rx.get("stderr") or ""), str(rx)[:200])

    print("artifact harvest collects only NEW files")
    rp = run_python({"code": "import matplotlib.pyplot as plt\nplt.plot([1,2,3]); plt.savefig('fig.png')\nprint('saved')"}, T1)
    check("savefig harvested as a plot", len(rp.get("plots") or []) == 1, str(rp.get("plots")))
    rp2 = run_python({"code": "print('no new plot this time')"}, T1)
    check("no re-harvest of the prior file (mtime filter)", len(rp2.get("plots") or []) == 0, str(rp2.get("plots")))
    # the file is still in the session cwd (copied, not moved) — agent can re-read it
    rread = run_python({"code": "import os; print('exists', os.path.exists('fig.png'))"}, T1)
    check("output file remains in session cwd (copied, not moved)", "exists True" in (rread.get("stdout") or ""))

    print("restart_kernel clears state")
    restart_kernel_tool({}, T1)
    rr = run_python({"code": "print(df)"}, T1)
    check("after restart, session state is gone → NameError",
          rr.get("returncode") != 0 and "NameError" in (rr.get("stderr") or ""), str(rr)[:200])

    from core.exec.kernels import get_pool
    get_pool().shutdown_all()

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL K1 run_python CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
