"""Phase 1 refinements: default-by-signal placement + EACCES-graceful fallback
(refs.md §3.3, §8). Single-project mode; one light network fetch.

Run:  .venv/bin/python tests/test_refs_placement.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_refplace_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rp.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "personal")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_GROUP"] = "lab"
_site = Path(_tmp) / "site.yaml"
_site.write_text(
    "site:\n  name: T\n"
    "refs:\n"
    f"  group: {_tmp}/grp/{{group}}/refs\n"
    f"  institution: {_tmp}/inst\n"
)
os.environ["ABA_SITE_CONFIG"] = str(_site)
_RS = Path(_tmp) / "refsources"
_RS.mkdir(parents=True, exist_ok=True)
(_RS / "test-phix.yaml").write_text(
    "provider: test-phix\nkind: manifest\nassets:\n"
    "  - role: genome\n    organism: phix\n    assembly: NC_001422\n"
    "    version: NC_001422.1\n"
    "    url: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    "?db=nuccore&id=NC_001422.1&rettype=fasta&retmode=text\n")
os.environ["ABA_REFSOURCES_DIR"] = str(_RS)
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import register_reference_tool, fetch_reference_tool  # noqa: E402

_failures: list[str] = []
GRP = Path(_tmp) / "grp" / "lab" / "refs"
INST = Path(_tmp) / "inst"


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()

    print("default-by-signal placement (no explicit scope)")
    ext = Path(_tmp) / "cluster_genome"
    ext.mkdir()
    (ext / "g.fa").write_text(">c\nACGT\n")
    rl = register_reference_tool({"path": str(ext), "organism": "fly", "role": "genome",
                                  "mode": "link"})   # linked → group
    check("linked ref defaults to group", rl.get("scope") == "group", str(rl)[:120])

    fa = Path(_tmp) / "x.gtf"
    fa.write_text("chr1\tt\tgene\t1\t9\t.\t+\t.\ti\n")
    rp = register_reference_tool({"path": str(fa), "organism": "fly", "role": "gtf"})  # no run
    check("plain copy with no open run defaults to personal", rp.get("scope") == "personal", str(rp)[:120])

    fb = Path(_tmp) / "y.bed"
    fb.write_text("chr1\t0\t9\n")
    re = register_reference_tool({"path": str(fb), "organism": "fly", "role": "blacklist",
                                  "scope": "institution"})  # explicit wins
    check("explicit scope is honored", re.get("scope") == "institution", str(re)[:120])
    check("no spurious warning when the explicit scope is writable", "warning" not in re)

    print("EACCES-graceful: a read-only tier falls back to personal + warns")
    INST.mkdir(parents=True, exist_ok=True)
    os.chmod(INST, 0o500)  # read+execute, no write — even for the owner
    try:
        fc = Path(_tmp) / "z.fa"
        fc.write_text(">d\nTTTT\n")
        rb = register_reference_tool({"path": str(fc), "organism": "fly", "role": "cds",
                                      "scope": "institution"})
        check("falls back to personal when the tier is unwritable", rb.get("scope") == "personal", str(rb)[:140])
        check("surfaces an actionable warning", "warning" in rb and "institution" in rb["warning"])
    finally:
        os.chmod(INST, 0o755)  # so the tempdir can be cleaned

    print("fetch_reference defaults to group (light network)")
    fr = fetch_reference_tool({"provider": "test-phix", "organism": "phix",
                               "assembly": "NC_001422", "role": "genome"})
    if fr.get("status") == "ok":
        from core.data import get_reference
        d = get_reference(fr["reference_id"]) or {}
        check("fetched standard reference defaults to group", d.get("scope") == "group", str(d.get("scope")))
    else:
        print(f"  [SKIP] fetch (network): {fr.get('error')}")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL REFS-PLACEMENT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
