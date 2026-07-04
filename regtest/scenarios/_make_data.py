#!/usr/bin/env python
"""Generate the realistic static data for the scenario library.

Run once from repo root: `.venv/bin/python regtest/scenarios/_make_data.py`
Writes each scenario's data into regtest/scenarios/<id>/data/. Fixed seeds → the
`expected` values in each scenario.yaml are reproducible. Realism notes:
  - counts use a negative-binomial (overdispersed), not raw Poisson
  - gene lengths + microbiome abundances are log-normal
  - survival times come from an exponential hazard that rises with EGFR
  - liftover uses REAL hg19 coordinates with known hg38 answers
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path

HERE = Path(__file__).parent


def d(scenario: str) -> Path:
    p = HERE / scenario / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def nb(rng, mean, size, dispersion=0.2):
    """Negative-binomial counts with a given mean and dispersion (var = mean + disp*mean^2)."""
    mean = np.asarray(mean, dtype=float)
    r = 1.0 / dispersion
    p = r / (r + mean)
    return rng.negative_binomial(r, p, size=size)


def make_bulk_de():
    rng = np.random.default_rng(42)
    n_genes = 2000
    base_mean = rng.lognormal(mean=4.0, sigma=1.3, size=n_genes)        # realistic per-gene means
    samples = ["ctrl_A1", "ctrl_A2", "ctrl_B1", "ctrl_B2", "trt_A1", "trt_A2", "trt_B1", "trt_B2"]
    cond = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    batch = np.array([1.0, 1.0, 1.3, 1.3, 1.0, 1.0, 1.3, 1.3])           # batch B ~30% deeper
    lfc = np.zeros(n_genes)
    de = rng.choice(n_genes, 150, replace=False)
    lfc[de] = rng.choice([-1, 1], 150) * rng.uniform(1.0, 3.0, 150)      # 150 DE genes, up & down
    M = np.zeros((n_genes, 8), dtype=int)
    for j in range(8):
        mu = base_mean * (2.0 ** (lfc * cond[j])) * batch[j]
        M[:, j] = nb(rng, mu, n_genes)
    df = pd.DataFrame(M, index=[f"GENE{i:04d}" for i in range(n_genes)], columns=samples)
    df.index.name = "gene"
    df.to_csv(d("bulk_de") / "counts.csv")
    meta = pd.DataFrame({"sample": samples,
                         "condition": ["control"] * 4 + ["treated"] * 4,
                         "batch": ["A", "A", "B", "B", "A", "A", "B", "B"]}).set_index("sample")
    meta.to_csv(d("bulk_de") / "samples.csv")
    n_up = int(((lfc > 0)).sum()); n_dn = int((lfc < 0).sum())
    print(f"bulk_de: {n_genes} genes, 150 DE ({n_up} up, {n_dn} down), 4v4 + batch covariate")


def make_tpm():
    rng = np.random.default_rng(7)
    n = 1000
    lengths = rng.lognormal(mean=7.5, sigma=0.6, size=n).astype(int).clip(300, 30000)  # bp
    base = rng.lognormal(mean=3.0, sigma=1.4, size=n)
    M = np.column_stack([nb(rng, base, n) for _ in range(4)])
    genes = [f"GENE{i:04d}" for i in range(n)]
    pd.DataFrame(M, index=genes, columns=[f"s{j}" for j in range(4)]).rename_axis("gene").to_csv(d("tpm")/"counts.csv")
    pd.DataFrame({"gene": genes, "length": lengths}).to_csv(d("tpm")/"lengths.csv", index=False)
    # expected top genes by mean TPM (deterministic)
    rate = M / lengths[:, None]
    tpm = rate / rate.sum(0) * 1e6
    top = pd.Series(tpm.mean(1), index=genes).sort_values(ascending=False).head(10)
    print("tpm: top-10 by mean TPM ->", list(top.index))


def make_survival():
    rng = np.random.default_rng(11)
    n = 150
    egfr = rng.normal(8.0, 1.6, n).clip(3, 14)
    hazard = np.exp(0.45 * (egfr - 8.0))                       # higher EGFR -> higher hazard
    t_event = rng.exponential(36.0 / hazard)                   # months
    t_cens = rng.uniform(0, 96, n)
    time = np.minimum(t_event, t_cens)
    event = (t_event <= t_cens).astype(int)
    pd.DataFrame({"time": time.round(2), "event": event, "EGFR_expression": egfr.round(2)}
                 ).to_csv(d("survival")/"clinical.csv", index=False)
    print(f"survival: n={n}, event rate={event.mean():.2f}; HIGH EGFR -> shorter survival (expected)")


def make_microbiome():
    rng = np.random.default_rng(13)
    taxa = 80
    cols, groups = [], []
    M = np.zeros((taxa, 20), dtype=int)
    for j in range(20):
        disease = j >= 10
        cols.append(f"S{j:02d}"); groups.append("disease" if disease else "healthy")
        n_present = rng.integers(20, 35) if disease else rng.integers(55, 75)   # disease: fewer taxa
        present = rng.choice(taxa, n_present, replace=False)
        ab = rng.lognormal(mean=3.0, sigma=(1.6 if disease else 0.9), size=n_present)  # disease: less even
        M[present, j] = ab.astype(int)
    df = pd.DataFrame(M, index=[f"OTU{i:03d}" for i in range(taxa)], columns=cols)
    df.index.name = "taxon"; df.to_csv(d("microbiome")/"otu.csv")
    pd.DataFrame({"sample": cols, "group": groups}).to_csv(d("microbiome")/"meta.csv", index=False)
    print("microbiome: 80 taxa x 20 samples (10 healthy / 10 disease); disease LOWER diversity (expected)")


def make_enrichment():
    genes = ["CDK1", "CCNB1", "CCNB2", "CCNA2", "CDC20", "BUB1", "BUB1B", "AURKA", "AURKB",
             "PLK1", "MKI67", "TOP2A", "BIRC5", "CENPA", "CENPE", "CENPF", "KIF11", "KIF23",
             "NDC80", "NUF2", "CCNE1", "CDC25C", "PTTG1", "FOXM1", "TPX2", "ESPL1", "CDK4",
             "MCM2", "MCM5", "PCNA"]
    (d("enrichment") / "genes.txt").write_text("\n".join(genes) + "\n")
    print(f"enrichment: {len(genes)} canonical cell-cycle/mitosis genes (expected enrichment: cell cycle/mitosis)")


def make_liftover():
    # REAL hg19 coordinates with documented hg38 mappings (BED is 0-based start).
    rows = [
        ("chr19", 45411940, 45412079, "rs7412_APOE"),     # APOE region; hg38 ~ chr19:44,908,*
        ("chr7", 55086714, 55086725, "EGFR_near_TSS"),    # hg38 ~ chr7:55,019,*
        ("chr17", 41196311, 41196500, "BRCA1_region"),    # hg38 ~ chr17:43,044,*
        ("chr12", 25398284, 25398320, "KRAS_codon12"),    # hg38 ~ chr12:25,245,*
    ]
    (d("liftover") / "positions.bed").write_text(
        "\n".join("\t".join(map(str, r)) for r in rows) + "\n")
    print("liftover: 4 REAL hg19 intervals (APOE/EGFR/BRCA1/KRAS); expect successful hg19->hg38, chrom preserved, ~0.5Mb shift")


def make_blast():
    # Aequorea victoria GFP (P42212) — unambiguous, well-known. Expect: GFP / Aequorea victoria.
    gfp = ("MSKGEELFTGVVPILVELDGDVNGHKFSVSGEGEGDATYGKLTLKFICTTGKLPVPWPTLVTTFSYGVQCFSRYPDHMKQHDFFKSAMPEGYVQERTIF"
           "FKDDGNYKTRAEVKFEGDTLVNRIELKGIDFKEDGNILGHKLEYNYNSHNVYIMADKQKNGIKVNFKIRHNIEDGSVQLADHYQQNTPIGDGPVLLPDN"
           "HYLSTQSALSKDPNEKRDHMVLLEFVTAAGITHGMDELYK")
    (d("blast_seq") / "mystery.fasta").write_text(f">unknown\n{gfp}\n")
    print("blast_seq: Aequorea victoria GFP (P42212); expect ID = GFP / Aequorea victoria")


if __name__ == "__main__":
    # NOTE: make_survival() is intentionally NOT called — survival/data is COMMITTED.
    # The v1->v2 upgrade expanded it (201 patients + age/sex/stage covariates for the
    # covariate-adjusted Cox model); this generator still emits the old 3-column/150-row
    # form, so running it would clobber the validated v2 data. survival/ stays committed.
    make_bulk_de(); make_tpm(); make_microbiome()
    make_enrichment(); make_liftover(); make_blast()
    print("\nDONE — data written under regtest/scenarios/<id>/data/ (survival is committed, not generated)")
