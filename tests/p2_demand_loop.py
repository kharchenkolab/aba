"""
P2′ demand loop: discover an uncatalogued PyPI library, propose it, auto-approve
(+ audit), materialize it, and import it from run_python — one session, one VM.
Isolated dirs (incl. throwaway ENVS_DIR overlay). Live network (PyPI).

Run:
    .venv/bin/python tests/p2_demand_loop.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p2_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p2.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_CAPABILITY_APPROVAL"] = "auto"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph.audit import list_events                     # noqa: E402
import content.bio  # noqa: E402,F401  (registers seed provider)
from core.catalog import resolve_capability                  # noqa: E402
from content.bio.tools import (                              # noqa: E402
    search_pypi, search_bioconda, propose_capability_tool, ensure_capability, run_python,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("search_pypi")
    r = search_pypi({"query": "numpy"})
    check("exact package found", r.get("found") and r.get("version"), str(r)[:120])
    rn = search_pypi({"query": "scikit_learn"})       # PEP-503 / separator variant
    check("name-variant resolves", rn.get("found") and "scikit" in (rn.get("name") or "").lower(), str(rn)[:120])
    rm = search_pypi({"query": "zzz-not-a-real-pkg-xyz"})
    check("missing package → found:false", rm.get("found") is False, str(rm)[:120])

    print("search_bioconda (awareness)")
    rb = search_bioconda({"query": "salmon"})
    check("bioconda tool discoverable + installable via propose/ensure",
          rb.get("found") and "ensure_capability" in (rb.get("note") or "").lower(), str(rb)[:160])

    print("propose: de-dupe against seed")
    rd = propose_capability_tool({"name": "gseapy"})  # already seeded
    check("seeded capability → already_available", rd.get("status") == "already_available", str(rd))

    print("propose: import_name plumbing (no install)")
    ri = propose_capability_tool({"name": "scikit-image", "import_name": "skimage"})
    check("uncatalogued lib → approved (auto)", ri.get("status") == "approved", str(ri))
    cap = resolve_capability("scikit-image")
    check("import_name persisted on the capability", cap and cap.get("import_name") == "skimage")

    print("full loop: propose → approve → materialize → import (live)")
    rp = propose_capability_tool({"name": "humanize"})   # tiny, pure-python, not in venv/seed
    check("propose humanize → approved", rp.get("status") == "approved", str(rp))
    # audit trail recorded
    kinds = [(e["kind"], e.get("title")) for e in list_events(limit=50)]
    check("capability_approved audit row present",
          any(k == "capability_approved" and t == "humanize" for k, t in kinds), str(kinds)[:200])
    re_ = ensure_capability({"name": "humanize"})
    check("ensure humanize → ready", re_.get("status") == "ready", str(re_))
    rpy = run_python({"code": "import humanize; print('humanize', humanize.__version__)"})
    check("run_python imports the on-demand library",
          rpy.get("returncode") == 0 and "humanize" in (rpy.get("stdout") or ""),
          f"rc={rpy.get('returncode')} stderr={rpy.get('stderr')!r}")

    print("ask-mode: proposal waits for approval")
    os.environ["ABA_CAPABILITY_APPROVAL"] = "ask"
    try:
        ra = propose_capability_tool({"name": "inflection"})
        check("ask-mode → pending_approval", ra.get("status") == "pending_approval", str(ra))
        rea = ensure_capability({"name": "inflection"})
        check("ensure refuses unapproved capability", rea.get("status") == "awaiting_approval", str(rea))
    finally:
        os.environ["ABA_CAPABILITY_APPROVAL"] = "auto"

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P2′ DEMAND-LOOP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
