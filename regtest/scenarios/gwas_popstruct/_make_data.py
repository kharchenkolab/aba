#!/usr/bin/env python
"""Deterministic data generator for the `gwas_popstruct` scenario.

Run:  /home/pkharchenko/aba/tools/scenario-venv/bin/python _make_data.py

Builds a small GWAS-style cohort with a PLANTED, checkable truth:

  - N = 400 individuals, M = 1000 biallelic SNPs, genotypes coded 0/1/2
    (count of the minor/alternate allele).
  - TWO subpopulations (POP_A = 200, POP_B = 200). Allele frequencies differ
    between the two populations, so the genotype matrix has clear structure:
    on a PCA, PC1 cleanly separates POP_A from POP_B.
  - A quantitative phenotype y built from THREE pieces:
        y = b_causal * (g_causal1 + g_causal2)        # 2 truly causal SNPs
            + delta_pop * 1[pop == POP_B]             # population offset (confounder)
            + noise
    The 2 causal SNPs have the SAME effect in both populations.
  - The population offset (delta_pop) confounds the naive association:
    every SNP whose frequency differs strongly between POP_A and POP_B will
    correlate with y through the population label, even though it has NO
    causal effect. So a naive (uncorrected) association test produces the
    2 causal hits PLUS a cloud of structure-driven FALSE positives.
  - Correcting for the top genotype PCs (which capture the POP_A/POP_B axis)
    removes the false positives while keeping the 2 causal SNPs.

Planted handles for checking (the truth is stamped into column/index names so
a grader can verify without re-deriving):
  - The 2 causal SNPs are named  rs_causal_1  and  rs_causal_2.
  - A run of "high-Fst" structure SNPs (large A/B frequency gap) are named
    rs_struct_### — these are the ones a naive test will FALSELY flag.
  - The hidden population label is written to pheno.csv column `pop_truth`
    (POP_A / POP_B). It is the answer key for "did PCA recover structure";
    a correct analysis must NOT use it as a covariate by name — it must
    discover structure from the genotypes (PCA) instead.

Files written into ./data/:
  genotypes.csv  — rows = individuals (IND_0000..), cols = 1000 SNP ids, values 0/1/2
  pheno.csv      — columns: iid, phenotype, pop_truth
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
DATA.mkdir(parents=True, exist_ok=True)

SEED = 0
rng = np.random.default_rng(SEED)

# ----- cohort dimensions -------------------------------------------------
N_A = 200            # individuals in population A
N_B = 200            # individuals in population B
N = N_A + N_B        # 400 total
M = 1000             # SNPs

# population label per individual (first N_A are A, next N_B are B)
pop = np.array(["POP_A"] * N_A + ["POP_B"] * N_B)
is_B = (pop == "POP_B").astype(float)

# ----- SNP frequency structure ------------------------------------------
# Background ancestral minor-allele frequency for each SNP.
anc = rng.uniform(0.1, 0.5, size=M)

# Per-population frequency differentiation. Most SNPs differ only mildly
# between A and B; a designated block of "structure" SNPs differ a LOT
# (high Fst) so they strongly track the population axis -> these become the
# naive false positives. We deliberately keep the 2 causal SNPs NON-
# differentiated (similar freq in both pops) so they are NOT explained away
# by structure -- they survive PC correction.
N_STRUCT = 120                                   # high-Fst structure SNPs
struct_idx = rng.choice(M, size=N_STRUCT, replace=False)

# Choose 2 causal SNPs from the NON-structure pool, with decent MAF in both pops.
nonstruct_pool = np.setdiff1d(np.arange(M), struct_idx)
# require a reasonably common ancestral freq so both pops carry the allele
common_pool = nonstruct_pool[(anc[nonstruct_pool] > 0.25) & (anc[nonstruct_pool] < 0.45)]
causal_idx = rng.choice(common_pool, size=2, replace=False)
causal_idx.sort()

# Build per-population frequencies.
freq_A = anc.copy()
freq_B = anc.copy()
# mild background drift everywhere
drift = rng.normal(0.0, 0.03, size=M)
freq_B = np.clip(freq_B + drift, 0.02, 0.98)
# strong, directional differentiation at the structure SNPs
big_gap = rng.uniform(0.30, 0.45, size=N_STRUCT) * rng.choice([-1.0, 1.0], size=N_STRUCT)
freq_B[struct_idx] = np.clip(freq_A[struct_idx] + big_gap, 0.02, 0.98)
# keep causal SNPs effectively un-differentiated (tiny gap only)
freq_B[causal_idx] = np.clip(freq_A[causal_idx] + rng.normal(0.0, 0.01, size=2), 0.02, 0.98)

# ----- draw genotypes (Hardy-Weinberg within each population) ------------
G = np.zeros((N, M), dtype=np.int8)
# population A block
pa = freq_A[None, :]
G[:N_A, :] = (rng.random((N_A, M)) < pa).astype(np.int8) + (rng.random((N_A, M)) < pa).astype(np.int8)
# population B block
pb = freq_B[None, :]
G[N_A:, :] = (rng.random((N_B, M)) < pb).astype(np.int8) + (rng.random((N_B, M)) < pb).astype(np.int8)

# ----- inject a few realistic QC blemishes -------------------------------
# (a) a handful of monomorphic / near-monomorphic SNPs (should fail MAF)
mono_idx = rng.choice(nonstruct_pool, size=15, replace=False)
mono_idx = np.setdiff1d(mono_idx, causal_idx)        # never blemish the causal SNPs
G[:, mono_idx] = 0
G[rng.integers(0, N, size=3)[:, None], mono_idx[:1]] = 1  # leave one barely-variable

# (b) a few SNPs grossly out of Hardy-Weinberg (excess heterozygosity ->
#     should fail an HWE filter). Force ~all hets at these sites.
hwe_idx = rng.choice(nonstruct_pool, size=8, replace=False)
hwe_idx = np.setdiff1d(hwe_idx, np.concatenate([causal_idx, mono_idx]))
G[:, hwe_idx] = 1
flip = rng.random((N, len(hwe_idx))) < 0.05
G[:, hwe_idx][flip] = rng.integers(0, 3, size=flip.sum()).astype(np.int8)

# (c) low-call-rate individuals + low-call-rate SNPs (missingness as -1 sentinel
#     would complicate CSV; instead we drop call rate by setting a missing mask
#     written as empty cells). Keep it simple + checkable: introduce missingness
#     as NaN in a small number of cells, concentrated on a few bad SNPs and a
#     few bad individuals.
geno = G.astype(float)
bad_snps = rng.choice(nonstruct_pool, size=6, replace=False)
bad_snps = np.setdiff1d(bad_snps, np.concatenate([causal_idx, mono_idx, hwe_idx]))
for s in bad_snps:
    miss_rows = rng.choice(N, size=int(0.25 * N), replace=False)   # 25% missing -> fails call rate
    geno[miss_rows, s] = np.nan
bad_inds = rng.choice(N, size=5, replace=False)
for r in bad_inds:
    miss_cols = rng.choice(M, size=int(0.30 * M), replace=False)   # 30% missing individual
    geno[r, miss_cols] = np.nan

# ----- build the phenotype ----------------------------------------------
# additive effect of the 2 causal SNPs (same in both populations)
b_causal = 1.5
g1 = G[:, causal_idx[0]].astype(float)
g2 = G[:, causal_idx[1]].astype(float)
genetic = b_causal * (g1 + g2)

# population offset: the confounder. POP_B individuals have a higher mean
# phenotype for reasons unrelated to any genotyped causal SNP.
delta_pop = 4.0
confound = delta_pop * is_B

noise = rng.normal(0.0, 2.0, size=N)
y = 10.0 + genetic + confound + noise

# ----- SNP ids: stamp the truth into the names ---------------------------
snp_ids = [f"rs{1000000 + i}" for i in range(M)]
for k, ci in enumerate(causal_idx, start=1):
    snp_ids[ci] = f"rs_causal_{k}"
for j, si in enumerate(sorted(struct_idx)):
    snp_ids[si] = f"rs_struct_{j:03d}"

iids = [f"IND_{i:04d}" for i in range(N)]

# ----- write genotypes.csv (individuals x SNPs) --------------------------
geno_df = pd.DataFrame(geno, index=iids, columns=snp_ids)
geno_df.index.name = "iid"
# integer-looking output where present; NaN -> empty cell (missing)
geno_df.to_csv(DATA / "genotypes.csv", float_format="%.0f")

# ----- write pheno.csv ---------------------------------------------------
pheno_df = pd.DataFrame({
    "iid": iids,
    "phenotype": np.round(y, 4),
    "pop_truth": pop,
})
pheno_df.to_csv(DATA / "pheno.csv", index=False)

# ----- report the planted truth -----------------------------------------
causal_names = [snp_ids[ci] for ci in causal_idx]
# observed MAF (population-pooled) at the causal SNPs, for sanity
maf_causal = [np.nanmean(geno[:, ci]) / 2.0 for ci in causal_idx]
print("=== gwas_popstruct planted truth ===")
print(f"N individuals = {N} (POP_A={N_A}, POP_B={N_B}); M SNPs = {M}")
print(f"Causal SNPs (true positives): {causal_names} at columns {list(causal_idx)}")
print(f"  per-allele effect b_causal = {b_causal} (same in both pops); pooled MAF ~ {np.round(maf_causal,3).tolist()}")
print(f"Population offset (confounder) delta_pop = {delta_pop} added to POP_B phenotype")
print(f"High-Fst STRUCTURE SNPs (naive false positives): {N_STRUCT} named rs_struct_### "
      f"(these track PC1; flagged by NAIVE test, dropped by PC-corrected test)")
print(f"QC blemishes: {len(mono_idx)} monomorphic/low-MAF, {len(hwe_idx)} HWE-violating "
      f"(excess het), {len(bad_snps)} low-call-rate SNPs, {len(bad_inds)} low-call-rate individuals")
print("PCA expectation: PC1 cleanly splits POP_A vs POP_B (two clusters).")

# sizes
for f in ["genotypes.csv", "pheno.csv"]:
    sz = (DATA / f).stat().st_size
    print(f"  wrote data/{f}: {sz:,} bytes")
