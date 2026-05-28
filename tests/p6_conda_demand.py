"""
P6: the demand loop now covers conda CLI tools. An uncatalogued bioconda tool
can be discovered, proposed (as archetype=cli → conda provisioning), approved,
materialized, and run. Isolated dirs; live network (bioconda solve is slow).

Run:
    .venv/bin/python tests/p6_conda_demand.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p6_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p6.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
from core.catalog import resolve_capability                  # noqa: E402
from content.bio.tools import (                              # noqa: E402
    search_bioconda, propose_capability_tool, ensure_capability, run_python,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("search_bioconda: now an install path (not stale)")
    rb = search_bioconda({"query": "bowtie2"})
    note = (rb.get("note") or "").lower()
    check("bioconda tool found", rb.get("found") is True, str(rb)[:120])
    check("note no longer says 'not installable'", "not installable" not in note)
    check("note points to propose + ensure",
          "propose_capability" in note and "ensure_capability" in note, note[:160])

    print("propose_capability(archetype='cli') → conda provisioning (deterministic)")
    rp = propose_capability_tool({"name": "bedtools", "archetype": "cli"})
    check("cli tool → approved", rp.get("status") == "approved" and rp.get("archetype") == "cli", str(rp))
    cap = resolve_capability("bedtools")
    prov = (cap or {}).get("provisioning") or {}
    check("provisioning is conda/bioconda",
          prov.get("conda", {}).get("channel") == "bioconda"
          and prov["conda"].get("spec") == "bedtools", str(prov))

    print("library archetype still defaults to pip")
    rl = propose_capability_tool({"name": "humanize"})
    cap_l = resolve_capability("humanize")
    check("library → pip provisioning",
          (cap_l or {}).get("provisioning", {}).get("pip") == ["humanize"], str(cap_l and cap_l.get("provisioning")))

    print("de-dupe: seeded conda tool")
    rd = propose_capability_tool({"name": "salmon", "archetype": "cli"})
    check("seeded salmon → already_available", rd.get("status") == "already_available", str(rd))

    print("live: discover → propose → ensure → run an uncatalogued bioconda tool (slow)")
    # csvtk: tiny Go binary (same author as seqkit), fast conda solve.
    propose_capability_tool({"name": "csvtk", "archetype": "cli"})
    re_ = ensure_capability({"name": "csvtk"})
    check("csvtk conda materialize → ready", re_.get("status") == "ready", str(re_))
    rr = run_python({"code": (
        "import subprocess\n"
        "out = subprocess.run(['csvtk', 'version'], capture_output=True, text=True)\n"
        "print('rc', out.returncode); print((out.stdout or out.stderr).strip())\n"
    ), "timeout_s": 60})
    check("on-demand bioconda tool runs from run_python",
          rr.get("returncode") == 0 and "csvtk" in (rr.get("stdout") or "").lower(),
          f"rc={rr.get('returncode')} stdout={rr.get('stdout')!r}")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P6 CONDA-DEMAND CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
