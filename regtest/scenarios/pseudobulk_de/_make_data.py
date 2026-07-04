"""Make a 6-donor multi-sample scRNA-seq AnnData for the pseudobulk_de scenario.

Cohort design (deterministic, seed=0):
  - 6 donors: ctrl_1..ctrl_3 (control) and treated_1..treated_3 (treated)
  - 3 cell types per donor: T, B, Myeloid
  - ~2500 cells total, 1000 genes
  - Raw integer counts in .X (what a pseudobulk DESeq2/edgeR flow expects)

Planted truth (the thing under test):
  - A treatment effect exists ONLY in the Myeloid compartment: a fixed set of
    100 genes shift between control and treated donors (a mix of up and down).
    T and B cells have NO condition effect (only noise) — so a correct
    pseudobulk-per-cell-type DE should find ~100 hits in Myeloid and ~none in T/B.
  - The effect is planted at the *donor* level (each donor gets a small jitter on
    the log-fold-change) so it survives donor-level pseudobulk aggregation and is
    NOT just a per-cell artifact.
  - ctrl_3 is a deliberate prep-failure outlier: every cell in ctrl_3 (all cell
    types) carries a global multiplicative library shift + a per-gene aberrant
    profile, so on a sample-level PCA ctrl_3 sits far from the other 5 donors.
    Removing ctrl_3 should TIGHTEN the control group and clean up Myeloid DE.

    .venv/bin/python regtest/scenarios/pseudobulk_de/_make_data.py
"""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import anndata as ad
from scipy import sparse

rng = np.random.default_rng(0)
HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "cohort.h5ad"
TRUTH = HERE / "data" / "truth.json"

N_GENES = 800
CELLTYPES = ["T", "B", "Myeloid"]
DONORS = ["ctrl_1", "ctrl_2", "ctrl_3", "treated_1", "treated_2", "treated_3"]
COND = {d: ("treated" if d.startswith("treated") else "control") for d in DONORS}

# Roughly how many cells of each type per donor (jittered a little per donor).
BASE_CELLS = {"T": 180, "B": 110, "Myeloid": 130}   # ~420 cells/donor -> ~2520 total

# --- baseline per-gene mean expression (shared across all cells) ---------------
# Heavy-tailed but LOW baseline: most genes are near-zero per cell (droplet scRNA
# is ~90% zeros), a few are highly expressed. Keeps the matrix genuinely sparse.
base_mu = rng.lognormal(mean=-2.3, sigma=1.3, size=N_GENES)
base_mu = np.clip(base_mu, 0.01, None)

# --- cell-type identity blocks: each type up-regulates a disjoint marker block --
MARKERS = 50
ct_marker_idx = {ct: np.arange(i * MARKERS, (i + 1) * MARKERS) for i, ct in enumerate(CELLTYPES)}

# --- PLANTED treatment effect: Myeloid only, 100 genes, mixed up/down -----------
# Pick 100 genes that are NOT in any cell-type marker block (genes 150..999),
# so the treatment signal is independent of identity markers.
candidate = np.arange(len(CELLTYPES) * MARKERS, N_GENES)   # 150..999
de_genes = rng.choice(candidate, size=100, replace=False)
de_genes.sort()
# Ensure the DE genes sit at a moderate baseline so the effect is powered at the
# pseudobulk level (very-low-count genes are unidentifiable), without making them
# the most-expressed genes in the panel (which would be a giveaway).
base_mu[de_genes] = np.maximum(base_mu[de_genes], rng.uniform(0.4, 1.2, size=100))
# Mixed direction: ~half up, ~half down. log2FC magnitude ~1.6-2.4 so the
# donor-level pseudobulk effect is robustly detectable by a real DE method
# (DESeq2/edgeR/pydeseq2) on 3-vs-3 donors, yet absent in T/B.
signs = np.where(rng.random(100) < 0.5, 1.0, -1.0)
log2fc = signs * rng.uniform(1.6, 2.4, size=100)
n_up = int(np.sum(log2fc > 0))
n_down = int(np.sum(log2fc < 0))

# Per-donor jitter on the planted log2FC (so it is a real donor-level effect,
# not a constant). Treated donors carry the effect; controls do not.
donor_lfc = {}
for d in DONORS:
    if COND[d] == "treated":
        jitter = rng.normal(0.0, 0.12, size=100)
        donor_lfc[d] = log2fc + jitter
    else:
        donor_lfc[d] = np.zeros(100)

# --- ctrl_3 prep-failure aberrant profile --------------------------------------
# Global library shift (cells get ~0.45x fewer counts) plus a per-gene aberrant
# multiplicative factor so the *profile shape* is off, not just total depth.
CTRL3_LIB_SHIFT = 0.45
ctrl3_gene_factor = rng.lognormal(mean=0.0, sigma=0.8, size=N_GENES)

DISP = 2.0   # NB dispersion (shape param of the Gamma in the Gamma-Poisson)

X_rows, obs_donor, obs_cond, obs_ct = [], [], [], []
for d in DONORS:
    for ct in CELLTYPES:
        # per-donor cell-count jitter
        n = BASE_CELLS[ct] + int(rng.integers(-15, 16))
        mu = base_mu.copy()
        # cell-type identity: boost this type's marker block
        mu[ct_marker_idx[ct]] *= 6.0
        # planted treatment effect: Myeloid only
        if ct == "Myeloid":
            fc = np.power(2.0, donor_lfc[d])      # linear fold-change per DE gene
            mu[de_genes] *= fc
        # ctrl_3 prep failure: aberrant per-gene profile + library shift (all cell types)
        if d == "ctrl_3":
            mu = mu * ctrl3_gene_factor * CTRL3_LIB_SHIFT
        # Gamma-Poisson (negative-binomial) counts
        gam = rng.gamma(shape=DISP, scale=mu / DISP, size=(n, N_GENES))
        counts = rng.poisson(gam).astype(np.int64)
        X_rows.append(counts)
        obs_donor += [d] * n
        obs_cond += [COND[d]] * n
        obs_ct += [ct] * n

X = np.vstack(X_rows)
# store as a CSR sparse integer matrix to keep the file small (counts are ~95% zero)
adata = ad.AnnData(X=sparse.csr_matrix(X.astype(np.int32)))
adata.obs["donor"] = obs_donor
adata.obs["condition"] = obs_cond
adata.obs["cell_type"] = obs_ct
# make the obs columns categorical (typical for scRNA AnnData)
for c in ("donor", "condition", "cell_type"):
    adata.obs[c] = adata.obs[c].astype("category")
adata.var_names = [f"gene_{i:04d}" for i in range(N_GENES)]
adata.obs_names = [f"cell_{i:05d}" for i in range(adata.n_obs)]

OUT.parent.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(OUT, compression="gzip", compression_opts=9)

truth = {
    "n_cells": int(adata.n_obs),
    "n_genes": int(N_GENES),
    "donors": DONORS,
    "condition": COND,
    "cell_types": CELLTYPES,
    "de_compartment": "Myeloid",
    "n_de_genes_myeloid": 100,
    "n_up": n_up,
    "n_down": n_down,
    "de_gene_names": [f"gene_{i:04d}" for i in de_genes.tolist()],
    "de_gene_log2fc": {f"gene_{i:04d}": float(f) for i, f in zip(de_genes.tolist(), log2fc.tolist())},
    "outlier_donor": "ctrl_3",
    "outlier_kind": "prep failure: global library shift (~0.45x) + aberrant per-gene profile",
    "non_de_compartments": ["T", "B"],
}
TRUTH.write_text(json.dumps(truth, indent=2))

# quick sanity: per-donor mean total counts (ctrl_3 should be clearly low)
import collections
rowsum = X.sum(1)
tot = collections.defaultdict(list)
for i, d in enumerate(obs_donor):
    tot[d].append(int(rowsum[i]))
print(f"wrote {OUT}  shape={adata.shape}")
print(f"  Myeloid DE genes: {len(de_genes)}  (up={n_up}, down={n_down})")
print(f"  per-donor mean total counts:")
for d in DONORS:
    print(f"    {d:10s} {np.mean(tot[d]):8.1f}")
print(f"wrote {TRUTH}")
