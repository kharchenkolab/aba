"""Phase 1: scoped reference tiers + registry-based (cross-context) discovery
(misc/refs.md §3.3, §8). Driven by a local site.yaml whose `refs.group` points
at a temp dir — exactly the OOD/VBC shape (`/groups/{group}/aba/refs`), but
local. Proves the headline: a group-scoped reference is discovered + resolved
from the shared registry independent of any per-project entity, which is what
makes cross-user/cross-project sharing work.

Run:  .venv/bin/python tests/test_refs_tiers.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_reftiers_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rt.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "personal_refs")   # the personal/default tier
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
# A local site.yaml exactly like the OOD/VBC one, but rooted under the temp dir.
os.environ["ABA_GROUP"] = "testlab"
_site = Path(_tmp) / "site.yaml"
_site.write_text(
    "site:\n  name: TestSite\n"
    "refs:\n"
    f"  group: {_tmp}/groups/{{group}}/aba/refs\n"
    f"  institution: {_tmp}/cluster/aba/refs\n"
)
os.environ["ABA_SITE_CONFIG"] = str(_site)
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph.entities import delete_entity_hard           # noqa: E402
from core.data import get_reference                          # noqa: E402
from core.data.refstore import _tier_roots                   # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import (                              # noqa: E402
    register_reference_tool, find_reference_tool, resolve_reference_tool,
)

_failures: list[str] = []
GROUP_ROOT = Path(_tmp) / "groups" / "testlab" / "aba" / "refs"
PERSONAL_ROOT = Path(_tmp) / "personal_refs"


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("tier resolution from site.yaml (the OOD/VBC shape, local)")
    tiers = dict(_tier_roots())
    check("personal + group + institution tiers resolve", {"personal", "group", "institution"} <= set(tiers),
          str({k: str(v) for k, v in tiers.items()}))
    check("group root has {group}=testlab expanded", tiers.get("group") == GROUP_ROOT,
          str(tiers.get("group")))

    print("scoped placement → register(scope=group) lands in the GROUP tier, not personal")
    fa = Path(_tmp) / "genome.fa"
    fa.write_text(">c\nACGTACGT\n")
    r = register_reference_tool({"path": str(fa), "organism": "fly", "role": "genome",
                                 "assembly": "BDGP6", "scope": "group"})
    rid = r["reference_id"]
    check("descriptor written under the group registry",
          (GROUP_ROOT / "registry" / f"{rid}.json").exists(), str(GROUP_ROOT))
    check("NOT written under the personal registry",
          not (PERSONAL_ROOT / "registry" / f"{rid}.json").exists())
    check("owned bytes under the group objects/ pool",
          bool(r.get("artifact_path"))
          and (GROUP_ROOT / "objects") in Path(r["artifact_path"]).parents,
          str(r.get("artifact_path")))

    print("registry-based discovery is ENTITY-INDEPENDENT (= cross-context / cross-user)")
    # Delete the per-project entity; discovery + resolution must still work,
    # because they read the shared registry — which is exactly the situation a
    # *different* user/project is in (no local entity, shared group tier).
    delete_entity_hard(rid)
    f = find_reference_tool({"organism": "fly", "role": "genome"})
    check("find still locates it after the entity is gone (registry, not entity)",
          f.get("found") and f["reference"]["id"] == rid, str(f)[:140])
    d = get_reference(rid)
    check("get_reference still returns the descriptor", bool(d) and d.get("scope") == "group")
    res = resolve_reference_tool({"reference_id": rid})
    check("resolve still returns the path (entity-independent)",
          res.get("status") == "ok" and res.get("local_path"), str(res)[:140])

    print("dedup across the group tier (registry-based)")
    r2 = register_reference_tool({"path": str(fa), "organism": "fly", "role": "genome",
                                  "scope": "group"})
    check("re-register same content (scope=group) → same id", r2.get("reference_id") == rid)

    print("a second reference at institution scope lands in the institution tier")
    gtf = Path(_tmp) / "ann.gtf"
    gtf.write_text("chr1\ttest\tgene\t1\t100\t.\t+\t.\tid\n")
    ri = register_reference_tool({"path": str(gtf), "organism": "fly", "role": "gtf",
                                  "scope": "institution"})
    inst_root = Path(_tmp) / "cluster" / "aba" / "refs"
    check("institution-scoped ref lands in the institution registry",
          (inst_root / "registry" / f"{ri['reference_id']}.json").exists())
    check("layered find sees both tiers (group genome + institution gtf)",
          len(find_reference_tool({"organism": "fly", "all": True}).get("references", [])) >= 2)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL REFS-TIERS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
