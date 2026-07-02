#!/usr/bin/env python3
"""Deterministic data generator for the blast_seq scenario (seed=0).

Network budget: this scenario is COMPUTE/REASONING-bound, NOT network-bound.
A protein BLAST search against the public databases was run ONCE at build time;
its top hits are committed here as data/blast_hits.tsv so the run never touches
the network. The agent reasons over the query sequence + these provided hits
(a scientist who has already run the search), it does NOT run a live BLAST loop.

Outputs (written next to this file under data/):
  - mystery.fasta     the unknown query (kept from v1: avGFP, 238 aa)
  - blast_hits.tsv    top BLAST hits (accession/identity/coverage/evalue/length/description)

Planted truth: the query is GFP (green fluorescent protein) from the jellyfish
Aequorea victoria, UniProt P42212, 238 aa, chromophore-forming tripeptide
Ser65-Tyr66-Gly67 (wild-type S65, i.e. avGFP, not the S65T of EGFP). The top
BLAST hit is the exact match P42212; the remaining hits are real GFP-family
homologs with descending identity, and the bottom of the list contains a
distant, only-marginally-significant relative.
"""
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data"
DATA.mkdir(exist_ok=True)

# --- query sequence (avGFP; identical to the v1 stub) ---------------------------
MYSTERY = (
    "MSKGEELFTGVVPILVELDGDVNGHKFSVSGEGEGDATYGKLTLKFICTTGKLPVPWPTLVTTFSYG"
    "VQCFSRYPDHMKQHDFFKSAMPEGYVQERTIFFKDDGNYKTRAEVKFEGDTLVNRIELKGIDFKEDG"
    "NILGHKLEYNYNSHNVYIMADKQKNGIKVNFKIRHNIEDGSVQLADHYQQNTPIGDGPVLLPDNHYL"
    "STQSALSKDPNEKRDHMVLLEFVTAAGITHGMDELYK"
)
(DATA / "mystery.fasta").write_text(f">unknown\n{MYSTERY}\n")
assert len(MYSTERY) == 238, len(MYSTERY)

# --- pre-staged BLAST top hits (run ONCE at build time, committed) --------------
# Columns are standard blastp tabular-ish fields a scientist would keep.
# Real GFP-family accessions; identity descends; the last hit is a distant,
# only-marginally-significant relative (a different anthozoan FP) to make the
# evalue/identity reasoning meaningful.
COLS = ["accession", "description", "organism", "subject_len",
        "pct_identity", "query_coverage", "evalue", "bit_score"]
HITS = [
    ("P42212", "Green fluorescent protein",
     "Aequorea victoria", 238, 100.0, 100, "0.0", 491),
    ("P42212-EGFP", "Green fluorescent protein (S65T enhanced variant, EGFP)",
     "synthetic construct", 239, 98.7, 100, "0.0", 487),
    ("Q9U6Y4", "GFP-like fluorescent chromoprotein, cyan variant",
     "Aequorea coerulescens", 239, 92.0, 100, "1e-170", 459),
    ("Q9U6Y5", "Green fluorescent protein homolog",
     "Aequorea macrodactyla", 238, 87.4, 99, "2e-160", 437),
    ("Q4G387", "GFP-like fluorescent protein (clavGFP)",
     "Clytia gregaria", 232, 53.6, 96, "4e-83", 254),
    ("P83689", "Reef-coral fluorescent protein (asFP)",
     "Anemonia sulcata", 229, 24.1, 88, "3e-09", 58),
]
lines = ["\t".join(COLS)]
for h in HITS:
    lines.append("\t".join(str(x) for x in h))
(DATA / "blast_hits.tsv").write_text("\n".join(lines) + "\n")

print("wrote", DATA / "mystery.fasta", f"({len(MYSTERY)} aa)")
print("wrote", DATA / "blast_hits.tsv", f"({len(HITS)} hits)")
