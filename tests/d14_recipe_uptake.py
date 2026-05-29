"""
Recipe-uptake nudge (systemic): when a run_python/run_r cell imports a library a
recipe covers (capabilities_needed) but that recipe wasn't read this turn, the
result carries a one-time hint to read it. Keyed on the code's imports — not a
fuzzy relevance score — with a foundational-lib denylist + a count cap, so common
libs (pandas/numpy/Matrix/matplotlib) don't false-trigger.

Deterministic; no model. Run:
    .venv/bin/python tests/d14_recipe_uptake.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d14_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "t.db")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "e")
os.environ["DATA_DIR"] = str(Path(_tmp) / "d")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
init_db()
import content.bio                                       # noqa: E402,F401
import content.bio.tools as T                            # noqa: E402
from core.skills.loader import recipes_for_capability    # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def hint(name, code, read=None, result=None, intent=None):
    rc = {"read": set(read or []), "nudged": False}
    r = dict(result or {"stdout": "", "stderr": "", "returncode": 0})
    ctx = {"recipe_ctx": rc}
    if intent is not None:
        ctx["intent"] = intent
    T._recipe_uptake_hint(name, {"code": code}, r, ctx)
    return r.get("recipe_hint"), rc


def test_capability_map():
    print("recipes_for_capability maps a specific lib → its recipe(s)")
    check("DESeq2 -> deseq2-r", recipes_for_capability("DESeq2") == ["deseq2-r"], str(recipes_for_capability("DESeq2")))
    check("pydeseq2 -> bulk-rnaseq-de", "bulk-rnaseq-de" in recipes_for_capability("pydeseq2"),
          str(recipes_for_capability("pydeseq2")))
    check("limma -> limma-voom", "limma-voom" in recipes_for_capability("limma"))
    check("case-insensitive", recipes_for_capability("deseq2") == ["deseq2-r"])
    check("pandas is ubiquitous (>4 recipes → filtered)", len(recipes_for_capability("pandas")) > 4,
          str(len(recipes_for_capability("pandas"))))


def test_nudge():
    print("nudge fires for specific libs, not foundational ones; once per turn")
    h, rc = hint("run_r", "library(DESeq2)\nlibrary(Matrix)")
    check("library(DESeq2) → hint names deseq2-r", bool(h) and "deseq2-r" in h, str(h))
    check("Matrix (foundational) not in hint", bool(h) and "Matrix" not in h, str(h))
    check("nudged flag set", rc["nudged"] is True)

    h2, _ = hint("run_python", "from pydeseq2.dds import DeseqDataSet")
    check("import pydeseq2 → bulk-rnaseq-de", bool(h2) and "bulk-rnaseq-de" in h2, str(h2))

    check("import pandas+numpy → no hint", hint("run_python", "import pandas\nimport numpy")[0] is None)
    check("matplotlib hist → no hint", hint("run_python", "import matplotlib.pyplot as plt")[0] is None)
    check("already-read recipe → no hint", hint("run_r", "library(DESeq2)", read=["deseq2-r"])[0] is None)
    check("timeout result (no returncode) → no hint",
          hint("run_python", "from pydeseq2 import x", result={"error": "timed out"})[0] is None)
    check("non-exec tool → no hint", hint("read_csv_info", "library(DESeq2)")[0] is None)

    # once per turn: a single shared recipe_ctx nudges only on the first qualifying call
    rc2 = {"read": set(), "nudged": False}
    r1 = {"returncode": 1}
    T._recipe_uptake_hint("run_r", {"code": "library(DESeq2)"}, r1, {"recipe_ctx": rc2})
    r2 = {"returncode": 0}
    T._recipe_uptake_hint("run_r", {"code": "library(limma)"}, r2, {"recipe_ctx": rc2})
    check("once per turn (2nd qualifying call silent)", bool(r1.get("recipe_hint")) and not r2.get("recipe_hint"))


def test_nudge_intent_filter():
    print("nudge requires the recipe to also match the turn intent (versatile-lib guard)")
    h_m, _ = hint("run_python", "from pydeseq2.dds import DeseqDataSet",
                  intent="bulk RNA-seq differential expression treated vs control")
    check("intent matches → nudge", bool(h_m) and "bulk-rnaseq-de" in h_m, str(h_m))
    h_x, _ = hint("run_python", "from pydeseq2.dds import DeseqDataSet",
                  intent="align protein sequences and build a phylogenetic tree")
    check("intent mismatch → no nudge (off-topic recipe suppressed)", h_x is None, str(h_x))


def main() -> int:
    test_capability_map()
    test_nudge()
    test_nudge_intent_filter()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL RECIPE-UPTAKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
