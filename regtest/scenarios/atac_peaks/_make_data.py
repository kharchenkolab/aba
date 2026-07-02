"""Generate a tiny bulk ATAC-seq scenario over a 200 kb window of chr1.

Deterministic (seed=0). Writes three files into data/:

  fragments.bed   BED3+1: chrom  start  end  read_count   (one row per fragment;
                  i.e. each row is a single sequenced fragment, no count column
                  semantics beyond presence — peak callers pile these up)
  reference.fa    the genomic sequence of the window (chr1:1,000,000-1,200,000)
                  with a CTCF consensus motif planted inside the STRONG peaks
  genes.bed       BED6 gene models (a handful) for nearest-gene assignment

Planted truth
-------------
WINDOW: chr1:1,000,000-1,200,000  (200,000 bp, 0-based half-open in BED)
8 true accessible loci at fixed centers, each a ~600 bp pileup of fragments,
with GRADED depth so a strict vs permissive cutoff give different counts:

    locus  center(0-based)  depth-rank  strength   has_CTCF_motif  nearest_gene
    L1     1,012,000        very strong   strong    yes             GENEA (TSS)
    L2     1,038,000        strong        strong    yes             GENEB (TSS)
    L3     1,061,000        strong        strong    yes             GENEC (TSS)
    L4     1,090,000        medium        strong    yes             GENEC (intra)
    L5     1,123,000        medium        strong    yes             GENED (TSS)
    L6     1,148,000        weak          weak      no              GENED (intra)
    L7     1,171,000        weak          weak      no              GENEE (TSS)
    L8     1,195,000        very weak     weak      no              GENEE (intra)

"strong" loci (L1-L5) carry a planted CTCF consensus and have high fragment
depth; "weak" loci (L6-L8) have low depth and NO motif.

A genome-wide-ish background of scattered fragments is added so naive
"any-coverage" calling does NOT trivially recover the 8 loci.

  * Permissive cutoff -> all 8 loci recovered (8 peaks).
  * Strict cutoff     -> only the 5 deep loci (L1-L5) survive (5 peaks).
  * CTCF motif enrichment over the strict peak set: motif present in all 5
    strict peaks, absent from background -> strong enrichment.

CTCF consensus used for the planted motif (a canonical 19-bp core):
    CCGCGNGGNGGCAG  ... we use the widely cited core  "CCACNAGGTGGCAG"? no.
We plant the well-known JASPAR-style CTCF core consensus:
    CCGCGNGGNGGCAG   (degenerate -> we instantiate a fixed 19mer below)

Total output well under 1 MB.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np

SEED = 0
rng = np.random.default_rng(SEED)

OUTDIR = Path(__file__).resolve().parent / "data"
OUTDIR.mkdir(parents=True, exist_ok=True)

CHROM = "chr1"
WIN_START = 1_000_000          # 0-based, BED start of the window
WIN_LEN = 200_000             # 200 kb
WIN_END = WIN_START + WIN_LEN  # 1,200,000

FRAG_LEN_MEAN = 120           # typical ATAC fragment length (sub-nucleosomal)
FRAG_LEN_SD = 35
READ_LEN = FRAG_LEN_MEAN      # we store fragment intervals

# ---- CTCF consensus motif (instantiate a fixed, non-degenerate 19-mer core) ----
# Canonical CTCF core motif (JASPAR MA0139.1 consensus-like). Fixed so it is
# searchable as an exact/near-exact string and clearly enriched in strong peaks.
CTCF_MOTIF = "CCGCGCCCCCTGGTGGCAG"   # 19 bp, GC-rich, CTCF-like core
assert len(CTCF_MOTIF) == 19

# ---- 8 planted loci: (name, center, n_fragments, strong?) ----
# n_fragments controls pileup depth; "strong" loci get the CTCF motif planted.
LOCI = [
    # name, center(0-based abs), n_frag, strong
    ("L1", 1_012_000, 900, True),
    ("L2", 1_038_000, 700, True),
    ("L3", 1_061_000, 620, True),
    ("L4", 1_090_000, 480, True),
    ("L5", 1_123_000, 430, True),
    ("L6", 1_148_000, 130, False),
    ("L7", 1_171_000, 110, False),
    ("L8", 1_195_000,  90, False),
]
PEAK_SPREAD = 110             # SD of fragment centers around the locus center (~600bp peak)

N_BACKGROUND = 4000           # scattered background fragments across the window


def make_fragments() -> list[tuple[str, int, int, int]]:
    rows: list[tuple[int, int]] = []   # (start, end) abs coords

    def emit(center: int, n: int):
        # fragment midpoints ~ Normal(center, PEAK_SPREAD); fragment lengths ~ Normal
        mids = rng.normal(center, PEAK_SPREAD, size=n)
        lens = np.clip(rng.normal(FRAG_LEN_MEAN, FRAG_LEN_SD, size=n), 40, 250)
        for m, L in zip(mids, lens):
            s = int(round(m - L / 2))
            e = int(round(m + L / 2))
            s = max(WIN_START, s)
            e = min(WIN_END, e)
            if e - s >= 30:
                rows.append((s, e))

    # planted loci
    for _name, center, n, _strong in LOCI:
        emit(center, n)

    # diffuse background: uniform fragment starts across the window
    bg_start = rng.integers(WIN_START, WIN_END - 250, size=N_BACKGROUND)
    bg_len = np.clip(rng.normal(FRAG_LEN_MEAN, FRAG_LEN_SD, size=N_BACKGROUND), 40, 250).astype(int)
    for s, L in zip(bg_start, bg_len):
        e = min(WIN_END, int(s) + int(L))
        if e - s >= 30:
            rows.append((int(s), e))

    rows.sort()
    return [(CHROM, s, e, 1) for (s, e) in rows]


def make_reference() -> str:
    """Random-ish but deterministic genomic sequence for the window; plant the
    CTCF motif at the center of each STRONG locus."""
    bases = np.array(list("ACGT"))
    # slightly GC-poor background like real intergenic DNA
    probs = np.array([0.30, 0.20, 0.20, 0.30])
    seq = rng.choice(bases, size=WIN_LEN, p=probs)

    motif = np.array(list(CTCF_MOTIF))
    for _name, center, _n, strong in LOCI:
        if not strong:
            continue
        rel = center - WIN_START
        s = rel - len(motif) // 2
        seq[s:s + len(motif)] = motif
    return "".join(seq.tolist())


def make_genes() -> list[tuple]:
    """BED6 gene models (chrom,start,end,name,score,strand).

    Placed so nearest-gene assignment has clear answers:
      GENEA  TSS near L1
      GENEB  TSS near L2
      GENEC  spans L3 (TSS) .. L4 (intragenic)
      GENED  spans L5 (TSS) .. L6 (intragenic)
      GENEE  spans L7 (TSS) .. L8 (intragenic)
    """
    return [
        # name      tss       gene span (start,end)            strand
        (CHROM, 1_011_500, 1_018_000, "GENEA", 0, "+"),
        (CHROM, 1_037_600, 1_044_000, "GENEB", 0, "+"),
        (CHROM, 1_060_500, 1_092_000, "GENEC", 0, "+"),   # TSS@~L3, body covers L4
        (CHROM, 1_122_500, 1_150_000, "GENED", 0, "+"),   # TSS@~L5, body covers L6
        (CHROM, 1_170_500, 1_198_000, "GENEE", 0, "+"),   # TSS@~L7, body covers L8
    ]


def main():
    frags = make_fragments()
    fa = make_reference()
    genes = make_genes()

    # fragments.bed
    fp = OUTDIR / "fragments.bed"
    with fp.open("w") as fh:
        for chrom, s, e, n in frags:
            fh.write(f"{chrom}\t{s}\t{e}\t{n}\n")

    # reference.fa  (60-char wrapped)
    rp = OUTDIR / "reference.fa"
    with rp.open("w") as fh:
        fh.write(f">{CHROM}:{WIN_START+1}-{WIN_END} window\n")
        for i in range(0, len(fa), 60):
            fh.write(fa[i:i + 60] + "\n")

    # genes.bed
    gp = OUTDIR / "genes.bed"
    with gp.open("w") as fh:
        for chrom, s, e, name, score, strand in genes:
            fh.write(f"{chrom}\t{s}\t{e}\t{name}\t{score}\t{strand}\n")

    total = sum(p.stat().st_size for p in (fp, rp, gp))
    print(f"wrote {fp.name}: {len(frags)} fragments, {fp.stat().st_size} bytes")
    print(f"wrote {rp.name}: {len(fa)} bp window, {rp.stat().st_size} bytes")
    print(f"wrote {gp.name}: {len(genes)} genes, {gp.stat().st_size} bytes")
    print(f"TOTAL {total} bytes ({total/1e6:.3f} MB)")
    print(f"CTCF motif planted in strong loci: {CTCF_MOTIF}")
    print(f"strong loci (deep, motif): L1-L5 ; weak loci (shallow, no motif): L6-L8")


if __name__ == "__main__":
    main()
