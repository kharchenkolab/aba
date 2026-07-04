"""Write the tiny variant_annotation VCF(s) for the variant_annotation scenario.

These are REAL, well-documented variants in BRCA1 / TP53 / CFTR / EGFR. The
trick that makes the version_change step bite: the POSITIONS written here are the
variants' GRCh37 (hg19) coordinates, but the VCF header DELIBERATELY declares
GRCh38. So consequence calls run on the header-declared build land the positions
in the WRONG place on the genome (different genes, different consequences); only
the calls on the correct build (GRCh37) recover the documented biology.

NETWORK BUDGET: the scenario is reasoning-bound, NOT network-bound. The
consequence annotations are PRE-STAGED as small local tables (data/vep_grch38.tsv
and data/vep_grch37.tsv) fetched ONCE at build time by the sibling script
_fetch_vep.py against the Ensembl VEP REST API (rest.ensembl.org and
grch37.rest.ensembl.org) on 2026-06-29 and COMMITTED. The scenario RUN reads the
VCF + those tables locally and never calls VEP per variant.

This script only writes the two VCF files (the inputs). Run _fetch_vep.py
afterwards to (re)generate the committed annotation tables from the same VARIANTS
list. The conseq_grch37 / conseq_grch38 strings in the table below are the
human-readable documentation of the planted truth; the AUTHORITATIVE machine-
readable calls (gene + consequence + VEP IMPACT) live in the committed *.tsv.
Note one nuance vs. a naive read: CFTR F508del (rs113993960) is an inframe_deletion,
which VEP rates IMPACT=MODERATE (not HIGH) - so on GRCh37 there are 3 VEP-HIGH
variants (CFTR G542X, TP53 R342*, BRCA1 5266dupC), the truncating set the tumour
board cares about. See scenario.yaml expected_overall.planted_truth for the full
per-variant table on both builds.

Deterministic: no randomness, no network in THIS script. Just writes the VCFs.

    tools/scenario-venv/bin/python regtest/scenarios/variant_annotation/_make_data.py
    tools/scenario-venv/bin/python regtest/scenarios/variant_annotation/_fetch_vep.py
"""
from __future__ import annotations
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "variants.vcf"
# Corrected re-export staged at the version_change step (s6): the SAME 24 positions
# with the build label fixed to GRCh37. Only the ##reference / ##source header lines
# differ from variants.vcf — the variant rows are byte-for-byte identical.
OUT_CORRECTED = Path(__file__).resolve().parent / "data" / "variants_grch37.vcf"

# (chrom, pos_hg19, id, ref, alt, gene, label, impact_tier, conseq_grch37, conseq_grch38_if_mislabeled)
#   impact_tier in {HIGH, MODERATE, LOW}  (MODERATE = missense; LOW = synonymous/non-coding)
#   pos_hg19 is the GRCh37 coordinate. The header lies and says GRCh38.
VARIANTS = [
    # --- HIGH impact (truncating / frameshift) ---
    ("7",  117227865, "rs74597325",  "C",    "T", "CFTR",  "G542X",     "HIGH",     "stop_gained",        "intron_variant"),
    ("7",  117199646, "rs113993960", "CTCT", "C", "CFTR",  "F508del",   "HIGH",     "inframe_deletion",   "intron_variant"),
    ("17", 7577022,   "rs397516436", "G",    "A", "TP53",  "R342*",     "HIGH",     "stop_gained",        "3_prime_UTR_variant"),
    ("17", 41209080,  "rs80357906",  "G",    "GC","BRCA1", "5266dupC",  "HIGH",     "frameshift_variant", "upstream_gene_variant"),
    # --- MODERATE impact (missense) — cancer-gene pathogenic ---
    ("17", 7578406,   "rs28934578",  "C",    "T", "TP53",  "R175H",     "MODERATE", "missense_variant",   "stop_gained"),
    ("17", 7577120,   "rs28934576",  "C",    "T", "TP53",  "R273H",     "MODERATE", "missense_variant",   "missense_variant"),
    ("17", 7577538,   "rs11540652",  "C",    "T", "TP53",  "R248Q",     "MODERATE", "missense_variant",   "3_prime_UTR_variant"),
    ("17", 41203088,  "rs41293463",  "A",    "C", "BRCA1", "M1775R",    "MODERATE", "missense_variant",   "intergenic_variant"),
    # --- MODERATE impact (missense) — non-cancer-gene / actionable elsewhere ---
    ("7",  55259515,  "rs121434568", "T",    "G", "EGFR",  "L858R",     "MODERATE", "missense_variant",   "upstream_gene_variant"),
    ("7",  55249071,  "rs121913428", "C",    "T", "EGFR",  "T790M",     "MODERATE", "missense_variant",   "intron_variant"),
    ("7",  117227860, "rs75527207",  "G",    "A", "CFTR",  "G551D",     "MODERATE", "missense_variant",   "intron_variant"),
    ("7",  117171029, "rs78655421",  "G",    "A", "CFTR",  "R117H",     "MODERATE", "missense_variant",   "intron_variant"),
    # --- MODERATE impact (missense) — common / likely-benign ---
    ("17", 7579472,   "rs1042522",   "G",    "C", "TP53",  "P72R",      "MODERATE", "missense_variant",   "5_prime_UTR_variant"),
    ("17", 41223094,  "rs1799966",   "T",    "C", "BRCA1", "S1613G",    "MODERATE", "missense_variant",   "upstream_gene_variant"),
    ("17", 41244435,  "rs16941",     "T",    "C", "BRCA1", "E1038G",    "MODERATE", "missense_variant",   "intergenic_variant"),
    ("17", 41244000,  "rs16942",     "T",    "C", "BRCA1", "K1183R",    "MODERATE", "missense_variant",   "downstream_gene_variant"),
    ("17", 41246481,  "rs1799950",   "T",    "C", "BRCA1", "Q356R",     "MODERATE", "missense_variant",   "upstream_gene_variant"),
    ("7",  55229255,  "rs2227983",   "G",    "A", "EGFR",  "R521K",     "MODERATE", "missense_variant",   "intergenic_variant"),
    ("7",  117199533, "rs213950",    "G",    "A", "CFTR",  "V470M",     "MODERATE", "missense_variant",   "intron_variant"),
    # --- LOW impact (synonymous / non-coding) ---
    ("7",  55249063,  "rs1050171",   "G",    "A", "EGFR",  "Q787Q",     "LOW",      "synonymous_variant", "intron_variant"),
    ("17", 41245466,  "rs1799949",   "G",    "A", "BRCA1", "S694S",     "LOW",      "synonymous_variant", "upstream_gene_variant"),
    ("17", 7578210,   "rs1800372",   "T",    "C", "TP53",  "synon",     "LOW",      "synonymous_variant", "missense_variant"),
    ("17", 7578115,   "rs1625895",   "T",    "C", "TP53",  "intronic",  "LOW",      "non_coding_transcript_exon_variant", "splice_region_variant"),
    ("7",  55268916,  "rs2293347",   "C",    "T", "EGFR",  "D994D",     "LOW",      "synonymous_variant", "intergenic_variant"),
]


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    lines.append("##fileformat=VCFv4.2")
    # The lie that drives the version_change step. Real coords are GRCh37.
    lines.append("##reference=GRCh38")
    lines.append('##source=core_facility_export_2026')
    lines.append('##INFO=<ID=GENE,Number=1,Type=String,Description="Submitter gene annotation (not authoritative)">')
    lines.append("##contig=<ID=7>")
    lines.append("##contig=<ID=17>")
    lines.append("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO")
    # Sort by chrom (numeric) then pos for a tidy, realistic file.
    rows = sorted(VARIANTS, key=lambda v: (int(v[0]), v[1]))
    for chrom, pos, vid, ref, alt, gene, label, tier, c37, c38 in rows:
        info = f"GENE={gene}"
        lines.append(f"{chrom}\t{pos}\t{vid}\t{ref}\t{alt}\t.\tPASS\t{info}")
    OUT.write_text("\n".join(lines) + "\n")
    # Corrected re-export: same rows, build label fixed to GRCh37.
    corrected = []
    for ln in lines:
        if ln.startswith("##reference="):
            corrected.append("##reference=GRCh37")
        elif ln.startswith("##source="):
            corrected.append("##source=core_facility_reexport_2026_corrected_build")
        else:
            corrected.append(ln)
    OUT_CORRECTED.write_text("\n".join(corrected) + "\n")
    n = len(rows)
    # NB: these tiers are the curated biological tier in VARIANTS, for documentation.
    # The AUTHORITATIVE per-variant VEP IMPACT is in the committed data/vep_*.tsv tables
    # (on GRCh37: 3 VEP-HIGH, since CFTR F508del is an inframe_deletion = MODERATE).
    n_high = sum(1 for v in VARIANTS if v[7] == "HIGH")
    n_mod = sum(1 for v in VARIANTS if v[7] == "MODERATE")
    n_low = sum(1 for v in VARIANTS if v[7] == "LOW")
    print(f"wrote {OUT}  variants={n}  (curated tiers: HIGH={n_high} MODERATE={n_mod} LOW={n_low})")
    print(f"size={OUT.stat().st_size} bytes")
    print(f"wrote {OUT_CORRECTED}  (GRCh37-corrected re-export, same rows)  size={OUT_CORRECTED.stat().st_size} bytes")
    print("run _fetch_vep.py to (re)generate data/vep_grch38.tsv and data/vep_grch37.tsv")


if __name__ == "__main__":
    main()
