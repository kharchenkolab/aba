"""
P4: content-addressed reference store — register (dedup), derived references with
lineage, find/resolve; plus light fetch primitives. CAS-core checks are
deterministic (no network); fetch checks hit small public endpoints.
Isolated dirs incl. a throwaway REFS_DIR.

Run:
    .venv/bin/python tests/p4_references.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p4_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p4.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "refs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph.provenance import upstream                   # noqa: E402
from core.data import DataHandle, resolve                    # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import (                              # noqa: E402
    register_reference_tool, find_reference_tool, fetch_url, lookup_sra_runinfo,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    refs_dir = Path(os.environ["ABA_REFS_DIR"])

    print("CAS core: register / dedup / derived / lineage (no network)")
    fasta = Path(_tmp) / "tiny.fa"
    fasta.write_text(">seq1\nACGTACGTACGT\n>seq2\nTTTTGGGGCCCC\n")
    r1 = register_reference_tool({"path": str(fasta), "organism": "phage_x",
                                  "role": "transcriptome", "source": "test"})
    check("register_reference → ok + sha", r1.get("status") == "ok" and r1.get("sha"), str(r1))
    check("reference stored under REFS_DIR", str(refs_dir) in (r1.get("artifact_path") or ""))

    # dedup: identical content → same entity, no second copy.
    r1b = register_reference_tool({"path": str(fasta), "organism": "phage_x", "role": "transcriptome"})
    check("dedup: identical content → same reference id", r1b.get("reference_id") == r1.get("reference_id"))

    # derived reference (stand-in index) with lineage to R1.
    idx = Path(_tmp) / "tiny.fa.fai"
    idx.write_text("seq1\t12\t6\t12\t13\nseq2\t12\t26\t12\t13\n")
    r2 = register_reference_tool({"path": str(idx), "organism": "phage_x", "role": "fai_index",
                                  "derived_from": r1["reference_id"]})
    check("derived reference registered", r2.get("status") == "ok"
          and r2.get("reference_id") != r1.get("reference_id"), str(r2))
    up = {n["id"] for n in upstream(r2["reference_id"])}
    check("lineage edge: index wasDerivedFrom transcriptome", r1["reference_id"] in up)

    print("find_reference + resolve")
    f1 = find_reference_tool({"organism": "phage_x", "role": "transcriptome"})
    check("find_reference locates the transcriptome", f1.get("found")
          and f1["reference"]["id"] == r1["reference_id"], str(f1)[:160])
    f2 = find_reference_tool({"organism": "phage_x", "role": "fai_index"})
    check("find_reference locates the derived index", f2.get("found")
          and f2["reference"]["id"] == r2["reference_id"])
    staged = resolve(DataHandle(r1["reference_id"]))
    check("resolve returns CAS path + content-sha version lock",
          str(refs_dir) in staged.local_path and staged.version_lock == r1.get("sha"),
          f"{staged.local_path} lock={staged.version_lock}")

    print("fetch_url (light, network)")
    ru = fetch_url({"url": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
                           "?db=nuccore&id=NC_001422.1&rettype=fasta&retmode=text",
                    "filename": "phix.fa"})
    check("fetch_url downloads to scratch", ru.get("status") == "ok"
          and ru.get("bytes", 0) > 0 and Path(ru.get("path", "")).exists(),
          str({k: ru.get(k) for k in ('status', 'bytes')}))
    # fetched file can be registered as a reference end-to-end.
    if ru.get("status") == "ok":
        rr = register_reference_tool({"path": ru["path"], "organism": "phiX174",
                                      "role": "genome", "source": "NCBI"})
        check("fetched file registers as a reference", rr.get("status") == "ok")

    print("lookup_sra_runinfo (light, network)")
    rs = lookup_sra_runinfo({"accession": "SRR1039508"})
    check("ENA run lookup returns fastq urls",
          rs.get("n_runs", 0) >= 1 and rs.get("runs") and rs["runs"][0].get("fastq_urls"),
          str(rs)[:200])

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P4 REFERENCE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
