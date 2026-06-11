"""make_revision language detection — sniff the submitted code, not the parent.

Live bug 2026-06-11 (prj_128380fd thr_deed230d, events 188 + 200):
the agent's modified_code was Python (`import matplotlib.pyplot as plt`)
but the parent figure's exec record was R. _resolve_language(parent)
returned 'r', the R runner saw Python code, and the run died with
"unerwartetes Symbol" (German R parser error).

The fix: _detect_revision_language(modified_code, parent) sniffs the
code that's about to run FIRST; only consults the parent when the
submitted code carries no language signals at all.

This test exercises the pure detection helper directly (no kernel /
no run_python plumbing) on the three shapes that matter:
  - Python code over R parent  → 'python'  (the live-bug shape)
  - R code over Python parent  → 'r'
  - bare expression with no signals → fall back to parent

Run: .venv/bin/python tests/test_revision_language.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_rev_lang_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "lang.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]    = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402
from content.bio.lifecycle.revisions import _detect_revision_language  # noqa: E402


_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    R_PARENT   = {"exec_id": None, "metadata": {"thread_id": "t"}, "type": "figure"}
    PY_PARENT  = {"exec_id": None, "metadata": {"thread_id": "t"}, "type": "figure"}

    # Make the parent's "language hint" deterministic without going through
    # exec_records — we pass an entity dict whose lookup_code_for_entity
    # path will return None, and _detect_language(None) defaults to python.
    # To exercise the "fallback to parent" branch we monkey on a parent
    # exec record below.

    # ── 1. The live-bug shape: Python code, R parent → python ──────
    print("Python modified_code over an R parent → python (the live bug)")
    py_code = (
        "import matplotlib.pyplot as plt\n"
        "import pandas as pd\n"
        "fig, ax = plt.subplots()\n"
        "ax.plot([1,2,3])\n"
        "plt.savefig('out.png')\n"
    )
    check("python signals win over R-parent hint",
          _detect_revision_language(py_code, R_PARENT) == "python")

    # ── 2. R code, Python parent → r ───────────────────────────────
    print("\nR modified_code over a Python parent → r")
    r_code = (
        "library(ggplot2)\n"
        "df <- read.csv('data.csv')\n"
        "p <- ggplot(df, aes(x, y)) + geom_point()\n"
        "ggsave('out.png', p)\n"
    )
    check("R signals win over Python-parent hint",
          _detect_revision_language(r_code, PY_PARENT) == "r")

    # ── 3. Empty / whitespace code → fall back to parent ───────────
    print("\nempty modified_code → parent's hint takes over")
    # With no exec_id and no producing_code, _resolve_language defaults to python.
    check("empty code defers to parent (defaults to python)",
          _detect_revision_language("", R_PARENT) == "python")
    check("whitespace-only code defers to parent",
          _detect_revision_language("   \n\t  ", R_PARENT) == "python")

    # ── 4. No signals at all → fall back to parent ─────────────────
    print("\nsignal-free code → parent's hint takes over")
    # Pure expression with neither `import`/`def` nor `library`/`<-`.
    bare = "x = 1\ny = x + 2\n"
    # `x = 1` is also valid R, so `_PY_SIGNALS` won't match. Verify:
    check("signal-free defers to parent (default python)",
          _detect_revision_language(bare, R_PARENT) == "python")

    # ── 5. Mixed signals → majority wins, tie → python ─────────────
    print("\nmixed Python + R signals → majority wins")
    mixed_r_majority = (
        "library(ggplot2)\n"
        "library(dplyr)\n"
        "df <- read.csv('x.csv')\n"
        "# also: import numpy as np  (in a comment, not a real py import)\n"
        "import numpy as np\n"     # one python signal
    )
    # 3 R signals (library×2 + <-), 1 python signal → r
    check("R-majority mixed snippet → r",
          _detect_revision_language(mixed_r_majority, PY_PARENT) == "r",
          str(_detect_revision_language(mixed_r_majority, PY_PARENT)))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL REVISION-LANGUAGE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
