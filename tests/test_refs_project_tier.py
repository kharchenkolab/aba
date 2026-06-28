"""Phase 1: the per-project refs tier (refs.md §3.3) — multi-project mode.
A project-scoped reference lives under that project's dir and is invisible to
other projects, while group-scoped refs are shared. Also checks the
default-by-signal 'run open → project' placement.

Run:  .venv/bin/python tests/test_refs_project_tier.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("ABA_DB_PATH", None)
os.environ.pop("ABA_DB_PATH_OVERRIDE", None)
_TMP = tempfile.mkdtemp(prefix="aba_refproj_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ["ABA_REFS_DIR"] = os.path.join(_TMP, "personal")
os.environ["ABA_ENVS_DIR"] = os.path.join(_TMP, "envs")
os.environ["ABA_GROUP"] = "lab"
_site = Path(_TMP) / "site.yaml"
_site.write_text("site:\n  name: T\nrefs:\n"
                 f"  group: {_TMP}/grp/{{group}}/refs\n")
os.environ["ABA_SITE_CONFIG"] = str(_site)
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                   # noqa: E402
from core.config import project_root                         # noqa: E402
from core.data import get_reference                          # noqa: E402
from core.data.refstore import _tier_roots                   # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import register_reference_tool, find_reference_tool  # noqa: E402
from content.bio.lifecycle.runs import open_run              # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    projects.init()
    A = projects.create_project("ProjA")["id"]
    B = projects.create_project("ProjB")["id"]

    projects.set_current(A)
    print(f"project tier resolves under the current project ({A})")
    tiers = dict(_tier_roots())
    check("project tier present + points under project A",
          tiers.get("project") == project_root(A) / "refs", str(tiers.get("project")))

    fa = Path(_TMP) / "projA.fa"
    fa.write_text(">a\nACGT\n")
    rp = register_reference_tool({"path": str(fa), "organism": "fly", "role": "genome",
                                  "scope": "project"})
    check("project-scoped ref lands in project A's tier", rp.get("scope") == "project", str(rp)[:120])
    check("descriptor under project A's refs dir",
          (project_root(A) / "refs" / "registry" / f"{rp['reference_id']}.json").exists())
    check("project A finds its own project-scoped ref",
          find_reference_tool({"organism": "fly", "role": "genome"}).get("found"))

    # A group-scoped ref registered while in A is shared with everyone.
    ga = Path(_TMP) / "shared.gtf"
    ga.write_text("chr1\tt\tgene\t1\t9\t.\t+\t.\ti\n")
    rg = register_reference_tool({"path": str(ga), "organism": "fly", "role": "gtf",
                                  "scope": "group"})
    check("group ref placed in the group tier", rg.get("scope") == "group")

    print(f"switch to project B ({B}) — isolation")
    projects.set_current(B)
    fB = find_reference_tool({"organism": "fly", "role": "genome"})
    check("project B does NOT see project A's project-scoped genome", not fB.get("found"), str(fB)[:100])
    fG = find_reference_tool({"organism": "fly", "role": "gtf"})
    check("project B DOES see the group-scoped gtf (shared tier)", fG.get("found"))

    print("default-by-signal: a run-derived artifact (run open) → project")
    projects.set_current(A)
    open_run("thr_proj", "build run")
    fd = Path(_TMP) / "derived.idx"
    fd.write_text("index-bytes")
    rd = register_reference_tool({"path": str(fd), "organism": "fly", "role": "star_index"},
                                 ctx={"thread_id": "thr_proj"})
    check("run-derived ref (no explicit scope) defaults to project", rd.get("scope") == "project", str(rd)[:120])

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL REFS-PROJECT-TIER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
