"""Phase 1: refsources is composed by the bundle loader (a peer to catalog),
not layered in refsources.py. Verifies override-by-provider-name (narrowest
scope wins) + provenance, using the SAME mechanism the rest of the bundle uses.

Run:  .venv/bin/python tests/test_bundle_refsources.py
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.loader import _compose_refsources, Provenance      # noqa: E402
from core.bundle.scope_resolver import ScopeBundle                  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _scope(name: str, label: str, root: Path, files: dict[str, str]) -> ScopeBundle:
    rdir = root / "knowhow" / "refsources"
    rdir.mkdir(parents=True, exist_ok=True)
    for fn, text in files.items():
        (rdir / fn).write_text(text)
    return ScopeBundle(name=name, label=label, path=root, present=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aba_bundlerefs_"))

    # system scope (floor): ensembl@v1 + ncbi
    sys_scope = _scope("system", "System", tmp / "sys", {
        "ensembl.yaml": "provider: ensembl\nkind: manifest\nassets:\n"
                        "  - {role: genome, organism: homo_sapiens, assembly: GRCh38, url: SYSTEM_URL}\n",
        "ncbi.yaml": "provider: ncbi\nkind: template\ncommand: 'datasets {accession}'\nroles: {genome: {}}\n",
    })
    # institution scope: OVERRIDE ensembl + add a NEW provider
    inst_scope = _scope("institution", "Institution", tmp / "inst", {
        "ensembl.yaml": "provider: ensembl\nkind: manifest\nassets:\n"
                        "  - {role: genome, organism: homo_sapiens, assembly: GRCh38, url: INSTITUTION_URL}\n",
        "acme.yaml": "provider: acme-mirror\nkind: manifest\nassets:\n"
                     "  - {role: genome, organism: homo_sapiens, assembly: GRCh38, url: ACME_URL}\n",
    })

    print("compose chain [system (broadest) → institution (narrowest)]")
    prov = Provenance()
    refs = _compose_refsources([sys_scope, inst_scope], prov)

    check("all three providers present", {"ensembl", "ncbi", "acme-mirror"} <= set(refs), str(set(refs)))
    check("ensembl OVERRIDDEN by institution (narrowest wins)",
          refs["ensembl"]["assets"][0]["url"] == "INSTITUTION_URL",
          refs["ensembl"]["assets"][0].get("url"))
    check("ncbi comes from the system floor (not shadowed)",
          refs["ncbi"]["command"] == "datasets {accession}")
    check("acme-mirror is the institution-only addition", "acme-mirror" in refs)

    print("provenance records effective scope + shadowing")
    check("ensembl effective_scope = institution",
          prov.refsources["ensembl"]["effective_scope"] == "institution", str(prov.refsources.get("ensembl")))
    check("ensembl shadowed_in includes system",
          "system" in prov.refsources["ensembl"]["shadowed_in"], str(prov.refsources.get("ensembl")))
    check("ncbi effective_scope = system",
          prov.refsources["ncbi"]["effective_scope"] == "system")

    print("a scope without knowhow/refsources/ contributes nothing (no crash)")
    empty = ScopeBundle(name="user", label="User", path=tmp / "nonexistent", present=True)
    refs2 = _compose_refsources([sys_scope, empty], prov)
    check("missing-dir scope is skipped gracefully", "ensembl" in refs2 and "acme-mirror" not in refs2)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL BUNDLE-REFSOURCES CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
