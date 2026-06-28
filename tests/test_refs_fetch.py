"""fetch_reference: recipe-pack-style source catalog + thin executor
(misc/refs.md §5.1). Deterministic checks (catalog load + resolution, no
network) plus one light end-to-end fetch of a tiny asset.

Run:  .venv/bin/python tests/test_refs_fetch.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_reffetch_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rf.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "refs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
# Test/override refsources dir (loader puts it FIRST, then the built-in seed,
# so the built-in aws-indexes/ncbi manifests are also visible).
_RS = Path(_tmp) / "refsources"
_RS.mkdir(parents=True, exist_ok=True)
os.environ["ABA_REFSOURCES_DIR"] = str(_RS)
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.data import get_reference                          # noqa: E402
from core.data.refsources import load_providers, resolve_asset  # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import fetch_reference_tool           # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


# A tiny test provider that resolves to a ~5 KB fasta (phiX), so the end-to-end
# fetch is real but cheap. Written into the override dir.
(_RS / "test-phix.yaml").write_text(
    "provider: test-phix\n"
    "kind: manifest\n"
    "assets:\n"
    "  - role: genome\n"
    "    organism: phix174\n"
    "    assembly: NC_001422\n"
    "    version: NC_001422.1\n"
    "    url: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    "?db=nuccore&id=NC_001422.1&rettype=fasta&retmode=text\n"
)


def main() -> int:
    init_db()

    print("catalog: built-in providers load (no network)")
    provs = load_providers()
    check("aws-indexes + ncbi seed manifests present", {"aws-indexes", "ncbi"} <= set(provs),
          str(sorted(provs)))
    check("override provider also visible", "test-phix" in provs)

    print("resolution: manifest (pre-built index) + template (cli) — deterministic")
    a = resolve_asset("aws-indexes", organism="homo_sapiens", assembly="GRCh38", role="bowtie2_index")
    check("manifest → url + unpack + version",
          a.get("url", "").startswith("https://") and a.get("unpack") == "zip"
          and a.get("version") == "GRCh38_noalt_as", str(a))
    t = resolve_asset("ncbi", role="genome", accession="GCF_000001405.40")
    check("template → datasets command with the accession",
          "datasets download genome accession GCF_000001405.40" in (t.get("command") or "")
          and "--include genome" in (t.get("command") or ""), str(t))
    try:
        resolve_asset("ncbi", role="genome")  # missing accession
        check("template without accession raises", False)
    except ValueError:
        check("template without accession raises", True)
    try:
        resolve_asset("nope-provider")
        check("unknown provider raises", False)
    except ValueError:
        check("unknown provider raises", True)

    print("executor: template provider returns a runnable command (not auto-run)")
    m = fetch_reference_tool({"provider": "ncbi", "role": "gtf", "accession": "GCF_000001405.40"})
    check("ncbi fetch → manual + command + version",
          m.get("status") == "manual" and "datasets download" in (m.get("command") or "")
          and m.get("version") == "GCF_000001405.40", str(m)[:160])

    print("executor: URL provider fetches + registers end-to-end (light network)")
    r = fetch_reference_tool({"provider": "test-phix", "organism": "phix174",
                              "assembly": "NC_001422", "role": "genome"})
    check("fetch_reference → ok + reference_id", r.get("status") == "ok" and r.get("reference_id"),
          str(r)[:160])
    if r.get("status") == "ok":
        d = get_reference(r["reference_id"]) or {}
        acq = d.get("acquisition") or {}
        check("re-runnable acquisition spec recorded (mode=fetch + provider + url)",
              acq.get("mode") == "fetch" and acq.get("provider") == "test-phix"
              and acq.get("url", "").startswith("https://"), str(acq)[:160])
        check("reference owned + has structural_path under fly/phix taxonomy",
              d.get("owned") is True and bool(d.get("structural_path")), str(d.get("structural_path")))
        check("bytes landed under objects/ (owned copy)",
              (Path(os.environ["ABA_REFS_DIR"]) / "objects") in Path(d.get("artifact_path", "/x")).parents)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL REFS-FETCH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
