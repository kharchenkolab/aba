"""
P1: capability catalog seeds, stays out of the user tree, and materializes a
library on demand into the wipeable overlay so run_python can import it.
Isolated dirs (incl. a temp ENVS_DIR so the overlay is throwaway). Live pip
install (network).

Run:
    .venv/bin/python tests/p1_capabilities.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p1_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p1.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")     # throwaway overlay
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                          # noqa: E402
from core.graph.entities import list_entities                   # noqa: E402
import content.bio  # noqa: E402,F401  (registers the seed provider)
from core.catalog import list_capabilities, resolve_capability  # noqa: E402
from content.bio.tools import ensure_capability, list_capabilities_tool, run_python  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("catalog: seed + visibility")
    caps = list_capabilities()                       # triggers lazy seed
    names = {c["name"] for c in caps}
    check("seed loaded (gseapy, salmon present)", {"gseapy", "salmon"} <= names, str(sorted(names)))
    check("search by query works", any(c["name"] == "gseapy"
          for c in list_capabilities(query="enrichment")))
    check("search by tag works", any(c["name"] == "salmon"
          for c in list_capabilities(tags=["quantification"])))

    # Capabilities are entities, but MUST NOT show in the user-facing tree/feed.
    tree_types = {e["type"] for e in list_entities()}
    check("capabilities hidden from entity feed/tree", "capability" not in tree_types,
          str(sorted(tree_types)))
    # ...but an explicit type_filter still returns them (the catalog path).
    check("explicit type_filter='capability' still returns them",
          len(list_entities(type_filter="capability")) >= 1)

    print("ensure_capability: deferred conda tool")
    r_salmon = ensure_capability({"name": "salmon"})
    check("conda CLI tool → deferred", r_salmon.get("status") == "deferred", str(r_salmon))

    print("ensure_capability: not found")
    check("unknown capability → not_found",
          ensure_capability({"name": "definitely_not_real"}).get("status") == "not_found")

    print("ensure_capability: materialize a library + import from run_python (live pip)")
    r = ensure_capability({"name": "pyfaidx"})       # small pure-Python lib
    check("pyfaidx materialized → ready", r.get("status") == "ready", str(r))
    # The very next run_python should import it from the overlay (.venv untouched).
    rp = run_python({"code": "import pyfaidx; print('pyfaidx', pyfaidx.__version__)"})
    check("run_python imports the materialized library",
          rp.get("returncode") == 0 and "pyfaidx" in (rp.get("stdout") or ""),
          f"rc={rp.get('returncode')} stdout={rp.get('stdout')!r} stderr={rp.get('stderr')!r}")

    print("list_capabilities tool")
    lc = list_capabilities_tool({"query": "rna-seq"})
    check("list_capabilities tool returns entries", len(lc.get("capabilities") or []) >= 1)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P1 CAPABILITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
