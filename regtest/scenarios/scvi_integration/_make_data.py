"""Make a tiny 2-batch scRNA-seq AnnData for the scvi_integration scenario.

Three shared cell types across two batches, with a deliberate multiplicative
batch effect so the batches separate in raw PCA — integration (scVI) should mix
them while keeping the cell types apart. Raw integer counts live in .X (what
scVI expects). Deterministic.

    .venv/bin/python regtest/scenarios/scvi_integration/_make_data.py
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import anndata as ad

rng = np.random.default_rng(7)
OUT = Path(__file__).resolve().parent / "data" / "adata.h5ad"

N_GENES = 500
CELLTYPES = {"Tcell": 320, "Bcell": 260, "Myeloid": 220}   # per batch
BATCHES = ["batchA", "batchB"]
MARKERS = 40            # marker genes per cell type
BASE_MU = 0.6          # baseline mean expression (NB)

# per-cell-type marker gene blocks (disjoint), upregulated when that type is present
ct_list = list(CELLTYPES)
marker_idx = {ct: np.arange(i * MARKERS, (i + 1) * MARKERS) for i, ct in enumerate(ct_list)}

# multiplicative batch effect: a per-gene factor, different per batch
batch_factor = {
    "batchA": np.ones(N_GENES),
    "batchB": rng.lognormal(mean=0.0, sigma=0.6, size=N_GENES),  # shifts ~half the genes
}

X_rows, obs_batch, obs_ct = [], [], []
for batch in BATCHES:
    for ct, n in CELLTYPES.items():
        mu = np.full(N_GENES, BASE_MU)
        mu[marker_idx[ct]] *= 8.0                 # markers high in their cell type
        mu = mu * batch_factor[batch]              # apply batch effect
        # negative-binomial counts (Gamma-Poisson), dispersion ~ 1.5
        shape = 1.5
        gam = rng.gamma(shape=shape, scale=mu / shape, size=(n, N_GENES))
        counts = rng.poisson(gam).astype(np.int64)
        X_rows.append(counts)
        obs_batch += [batch] * n
        obs_ct += [ct] * n

X = np.vstack(X_rows)
adata = ad.AnnData(X=X.astype("float32"))
adata.obs["batch"] = obs_batch
adata.obs["cell_type_truth"] = obs_ct          # hidden truth, for checking integration quality
adata.var_names = [f"gene_{i}" for i in range(N_GENES)]
adata.obs_names = [f"cell_{i}" for i in range(adata.n_obs)]
OUT.parent.mkdir(parents=True, exist_ok=True)
adata.write_h5ad(OUT)
print(f"wrote {OUT}  shape={adata.shape}  batches={BATCHES}  types={list(CELLTYPES)}")
