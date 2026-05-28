"""
P0 Stage 2 regression: run_python now executes in the project scratch
workspace via LocalSubprocessExecutor, while keeping the plots/tables return
contract the registration hook depends on. Isolated dirs; no server, no model.

Run:
    .venv/bin/python tests/p0_run_python.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p0rp_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p0.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_KERNEL_ENABLED"] = "0"   # this suite tests the stateless one-shot lane
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                 # noqa: E402
from core.config import ARTIFACTS_DIR, WORK_DIR        # noqa: E402
from content.bio.tools import run_python               # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("run_python: plot output")
    r = run_python({"code": (
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2, 3]); plt.title('Spine Test')\n"
        "plt.savefig('fig.png')\n"
        "print('plot-done')\n"
    )})
    check("returncode 0", r.get("returncode") == 0, str(r))
    check("stdout captured", "plot-done" in (r.get("stdout") or ""))
    check("one plot returned", len(r.get("plots") or []) == 1, str(r.get("plots")))
    if r.get("plots"):
        name = Path(r["plots"][0]["url"]).name
        check("plot landed in artifact store", (ARTIFACTS_DIR / name).exists())
        check("plot url is /artifacts/*", r["plots"][0]["url"].startswith("/artifacts/"))

    print("run_python: csv output")
    r2 = run_python({"code": (
        "import pandas as pd\n"
        "pd.DataFrame({'a': [1, 2]}).to_csv('out.csv', index=False)\n"
    )})
    check("one table returned", len(r2.get("tables") or []) == 1, str(r2.get("tables")))

    print("run_python: scratch workspace persists")
    # A scratch run dir with the executed script should exist under WORK_DIR
    # (not deleted — the agent can revisit it).
    scripts = list(WORK_DIR.rglob("script.py"))
    check("scratch script.py persisted under WORK_DIR", len(scripts) >= 1,
          f"found {len(scripts)} under {WORK_DIR}")
    check("scratch is project-scoped (.../<project>/<run>/)",
          any(len(p.relative_to(WORK_DIR).parts) == 3 for p in scripts))

    print("run_python: error + timeout paths")
    rerr = run_python({"code": "raise ValueError('boom')"})
    check("python error → nonzero returncode + stderr", rerr.get("returncode") != 0
          and "boom" in (rerr.get("stderr") or ""))
    rto = run_python({"code": "import time; time.sleep(10)", "timeout_s": 1})
    check("timeout → error message", "timed out" in (rto.get("error") or ""))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P0 run_python CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
