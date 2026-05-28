"""
Discovery coverage — the failure behind the GEO sessions was the agent NOT
engaging discovery (it never called search_skills / search_pypi / list_capabilities
and hand-rolled scraping instead). This checks the discovery machinery is sound
and broad, so "discover first" actually pays off across domains:

  A. search_skills surfaces the right RECIPE for diverse intents (top-3).
  B. capability resolution: every seeded capability resolves; intent search over
     the catalog ranks the right package.
  C. (best-effort, network) search_pypi / search_bioconda find real packages.

Deterministic for A/B (no model); C is skipped if offline. Isolated temp DB —
never a live project DB. Run:
    .venv/bin/python tests/d9_discovery_coverage.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["ABA_DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="aba_disc9_"), "t.db")

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
from core.skills import search_skills  # noqa: E402
from core.catalog import resolve_capability, search_capabilities  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


# ---- A. recipe discovery: intent -> expected recipe(s) in the top-3 ----
SKILL_CASES = [
    ("download the processed count matrices for a GEO sample GSM", ("fetch-geo-processed-matrices",)),
    ("list the samples and their metadata in a GEO series GSE", ("query-geo", "fetch-geo-processed-matrices")),
    ("download raw FASTQ reads for an SRA accession", ("fetch-sequencing-fastq",)),
    ("quantify fastq reads into a single-cell count matrix", ("quantify-fastq-to-counts-kb",)),
    ("QC and cluster a single-cell RNA-seq dataset", ("scrna-qc-clustering",)),
    ("differential expression between two groups in bulk RNA-seq", ("bulk-rnaseq-de",)),
    ("integrate multiple scRNA-seq samples into a joint graph", ("conos-integration",)),
    ("call ChIP-seq peaks", ("chipseq-peak-calling-macs3",)),
    ("liftover genomic coordinates between assemblies", ("liftover-coordinates",)),
    ("call somatic mutations from a tumor BAM", ("detect-and-annotate-somatic-mutations",)),
    ("flux balance analysis of a metabolic model", ("perform-flux-balance-analysis",)),
    ("register these files as a dataset and pin the figure", ("manage-entities",)),
]


def test_skill_discovery():
    print("A. recipe discovery (search_skills, top-3)")
    for intent, expected in SKILL_CASES:
        top = [s.name for s in search_skills(intent, limit=3)]
        check(f"'{intent[:46]}' -> {expected[0]}", any(e in top for e in expected), str(top))


# ---- B1. every seeded capability resolves (the funnel can install it) ----
SEEDED = ["GEOparse", "pysradb", "ffq", "gget", "kb-python", "sra-tools",
          "salmon", "star", "fastqc", "seqkit", "cutadapt", "multiqc",
          "gseapy", "pydeseq2", "biopython", "pyfaidx"]


def test_capability_resolution():
    print("B1. seeded capabilities resolve")
    for name in SEEDED:
        c = resolve_capability(name)
        prov = (c or {}).get("provisioning") if c else None
        check(f"resolve_capability('{name}')", bool(c) and bool(prov), "not found / no provisioning")


# ---- B2. intent -> expected capability ranks in the catalog (top-5) ----
CAP_CASES = [
    ("differential expression bulk rna-seq", "pydeseq2"),
    ("gene set enrichment pathway analysis", "gseapy"),
    ("download GEO supplementary count matrices", "GEOparse"),
    ("resolve an SRA accession to fastq download links", "pysradb"),
    ("transcript-level rna-seq quantification", "salmon"),
    ("manipulate fasta and fastq files", "seqkit"),
]


def test_capability_search():
    print("B2. capability intent search (catalog, top-5)")
    for intent, expected in CAP_CASES:
        hits = [c.get("name") for c in search_capabilities(intent, limit=5)]
        check(f"'{intent[:42]}' -> {expected}", expected in hits, str(hits))


# ---- C. best-effort network discovery (skipped offline) ----
def test_network_discovery():
    print("C. PyPI / bioconda discovery (best-effort, network)")
    from content.bio.tools import search_pypi, search_bioconda
    try:
        r = search_pypi({"name": "scanpy"})
        if not (r.get("exists") or r.get("found")):
            print("  [skip] network unavailable / PyPI miss"); return
        check("search_pypi('scanpy') found", True)
        rb = search_bioconda({"name": "samtools"})
        check("search_bioconda('samtools') found", bool(rb.get("exists") or rb.get("found")), str(rb)[:120])
    except Exception as e:  # noqa: BLE001
        print(f"  [skip] network discovery unavailable: {e}")


def main() -> int:
    test_skill_discovery()
    test_capability_resolution()
    test_capability_search()
    test_network_discovery()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures)); return 1
    print("ALL DISCOVERY-COVERAGE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
