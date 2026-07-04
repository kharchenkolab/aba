"""Make a tiny PBMC-like single-sample scRNA-seq AnnData for scrna_qc_clustering.

ONE sample (no batches). RAW integer counts in .X. Nothing precomputed in obs
except a hidden `cell_type_truth` we keep only for checking the agent's work.

Planted structure (the ground truth the scenario checks against):
  - exactly 3 real populations, each with a disjoint marker program:
        T cell  : CD3D, CD3E, TRAC
        B cell  : MS4A1, CD79A, CD79B
        Myeloid : LYZ, CD14, FCGR3A
  - a block of mitochondrial genes named MT-*  (used for pct_counts_mt)
  - ~12% low-quality cells: high mito fraction + few detected genes
  - a few doublets: a T cell and a Myeloid cell summed (express both programs)

Deterministic: seed=0. Keeps the file < 2 MB.

    tools/scenario-venv/bin/python regtest/scenarios/scrna_qc_clustering/_make_data.py
"""
from __future__ import annotations

from pathlib import Path

import anndata as ad
import numpy as np
import scipy.sparse as sp

rng = np.random.default_rng(0)
OUT = Path(__file__).resolve().parent / "data" / "pbmc_like.h5ad"

# ---------------------------------------------------------------- dimensions
N_GENES = 1200
# good-quality cells per real population
N_GOOD = {"Tcell": 560, "Bcell": 380, "Myeloid": 360}   # 1300 good
N_LOWQ = 180          # ~12% clearly dead/dying (very high mito) — caught at any sane cutoff
N_STRESSED = 120      # mildly elevated mito (~12-30%): kept by a lenient pass, dropped at ~10%
N_DOUBLET = 20        # a few obvious doublets
# nominal total = 1300 + 180 + 120 + 20 = 1620; ~12% are the clearly-dead lowq cells

# ---------------------------------------------------------------- gene layout
# canonical lineage markers (real PBMC marker symbols)
MARKERS = {
    "Tcell": ["CD3D", "CD3E", "TRAC"],
    "Bcell": ["MS4A1", "CD79A", "CD79B"],
    "Myeloid": ["LYZ", "CD14", "FCGR3A"],
}
N_MT = 13                      # mitochondrial genes -> MT-ND1, MT-CO1, ...
MT_TAGS = ["ND1", "ND2", "CO1", "CO2", "ATP8", "ATP6", "CO3",
           "ND3", "ND4L", "ND4", "ND5", "ND6", "CYB"]
assert len(MT_TAGS) == N_MT

# Build the gene-name list: markers first, then MT- genes, then filler "background" genes.
marker_names = [g for gs in MARKERS.values() for g in gs]          # 9
mt_names = [f"MT-{t}" for t in MT_TAGS]                            # 13
n_named = len(marker_names) + len(mt_names)
filler_names = [f"GENE{i:04d}" for i in range(N_GENES - n_named)]
var_names = marker_names + mt_names + filler_names
assert len(var_names) == N_GENES
name_to_idx = {g: i for i, g in enumerate(var_names)}

mt_idx = np.array([name_to_idx[g] for g in mt_names])
marker_idx = {ct: np.array([name_to_idx[g] for g in gs]) for ct, gs in MARKERS.items()}

# ---------------------------------------------------------------- mean program
BASE_MU = 0.10          # baseline mean expression for background genes (NB)
MARKER_MU = 6.0         # marker mean expression in the matching cell type
MT_MU_HEALTHY = 0.35    # modest mito expression in healthy cells
SHAPE = 2.0             # NB dispersion (Gamma-Poisson)


def nb_counts(mu_vec: np.ndarray, n: int) -> np.ndarray:
    """n x N_GENES negative-binomial (Gamma-Poisson) counts with per-gene mean mu_vec."""
    gam = rng.gamma(shape=SHAPE, scale=mu_vec / SHAPE, size=(n, mu_vec.size))
    return rng.poisson(gam).astype(np.int64)


def base_mu_for(ct: str) -> np.ndarray:
    mu = np.full(N_GENES, BASE_MU)
    mu[marker_idx[ct]] = MARKER_MU            # turn on this lineage's markers
    mu[mt_idx] = MT_MU_HEALTHY                # healthy mito level
    return mu


X_rows: list[np.ndarray] = []
obs_ct: list[str] = []

# ---- good-quality cells of each real population -----------------------------
for ct, n in N_GOOD.items():
    X_rows.append(nb_counts(base_mu_for(ct), n))
    obs_ct += [ct] * n

# ---- low-quality cells -------------------------------------------------------
# Drawn from the real populations (so they would otherwise be a cell type) but
# damaged: very high mitochondrial fraction + few detected genes (low complexity).
lowq_src = rng.choice(list(N_GOOD), size=N_LOWQ, p=[0.45, 0.30, 0.25])
for ct in lowq_src:
    mu = base_mu_for(ct)
    mu = mu * 0.15                            # globally low capture -> few genes
    mu[mt_idx] = 9.0                          # blown-up mito expression
    X_rows.append(nb_counts(mu, 1))
obs_ct += ["lowq"] * N_LOWQ

# ---- mildly stressed cells ---------------------------------------------------
# Real cell types with moderately elevated mito (~12-30%) and slightly reduced
# complexity. A lenient first-pass mito cutoff keeps them; a stricter ~10% cutoff
# removes them -> the revision step genuinely drops cells without losing a type.
stress_src = rng.choice(list(N_GOOD), size=N_STRESSED, p=[0.45, 0.30, 0.25])
for ct in stress_src:
    mu = base_mu_for(ct)
    mu = mu * 0.7                            # slightly lower complexity
    mu[mt_idx] = 1.6                         # elevated-but-not-dead mito level
    X_rows.append(nb_counts(mu, 1))
obs_ct += ["stressed"] * N_STRESSED

# ---- doublets ----------------------------------------------------------------
# Sum a T-cell program and a Myeloid program -> co-express two lineages.
for _ in range(N_DOUBLET):
    a = nb_counts(base_mu_for("Tcell"), 1)
    b = nb_counts(base_mu_for("Myeloid"), 1)
    X_rows.append(a + b)
obs_ct += ["doublet"] * N_DOUBLET

X = np.vstack(X_rows)

# ---- shuffle rows so populations aren't in contiguous blocks -----------------
perm = rng.permutation(X.shape[0])
X = X[perm]
obs_ct = [obs_ct[i] for i in perm]

adata = ad.AnnData(X=sp.csr_matrix(X.astype("float32")))
adata.var_names = var_names
adata.obs_names = [f"cell_{i:04d}" for i in range(adata.n_obs)]
adata.obs["cell_type_truth"] = obs_ct        # HIDDEN truth — for checking only

OUT.parent.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(OUT)

# ---- report planted truth ----------------------------------------------------
import collections

mt_frac = np.asarray(X[:, mt_idx].sum(1)).ravel() / np.maximum(X.sum(1), 1)
n_genes = (X > 0).sum(1)
counts = collections.Counter(obs_ct)
sz = OUT.stat().st_size
lab = np.array(obs_ct)
healthy = np.isin(lab, ["Tcell", "Bcell", "Myeloid"])
print(f"wrote {OUT}  shape={adata.shape}  size={sz/1e6:.2f} MB")
print("truth labels:", dict(counts))
print(f"clearly-dead (lowq) fraction: {counts['lowq']/adata.n_obs:.3f}")
print(f"mito%% — healthy median: {np.median(mt_frac[healthy])*100:.1f}  "
      f"stressed median: {np.median(mt_frac[lab=='stressed'])*100:.1f}  "
      f"lowq median: {np.median(mt_frac[lab=='lowq'])*100:.1f}")
print(f"n_genes — healthy median: {np.median(n_genes[healthy]):.0f}  "
      f"stressed median: {np.median(n_genes[lab=='stressed']):.0f}  "
      f"lowq median: {np.median(n_genes[lab=='lowq']):.0f}")
for cut in [50, 20, 10]:
    rem = (mt_frac * 100) >= cut
    print(f"mito>={cut}%% removed: {rem.sum()} ({rem.mean()*100:.1f}%%)")
