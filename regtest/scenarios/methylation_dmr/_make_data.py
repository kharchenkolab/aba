"""Make a tiny methylation beta-value dataset for the methylation_dmr scenario.

A 3000-CpG x 20-sample beta matrix (10 case, 10 control). Most probes are
"background" with no group difference. Exactly 50 probes are planted as
DIFFERENTIALLY METHYLATED, all HYPERmethylated in cases, and those 50 probes
sit inside 3 tight genomic neighbourhoods (DMRs) near 3 named genes.

Geometry is chosen so region collapsing gives DIFFERENT counts at two distance
thresholds: within each DMR the probes form sub-clusters separated by gaps that
are > 1 kb but < 5 kb. So:
  - collapsing within 5 kb  -> the 3 DMRs stay whole       -> 3 regions
  - collapsing within 1 kb  -> each DMR splits into pieces  -> 7 regions

manifest.csv maps every probe -> (chrom, pos, gene). manifest_v2.csv is the
same EXCEPT a defined subset of DM probes is REMAPPED to new positions and new
gene names (a real-world re-annotation / liftover event). Re-annotating the DM
hits against v2 therefore changes a KNOWN set of gene assignments.

Deterministic (seed=0).

    tools/scenario-venv/bin/python regtest/scenarios/methylation_dmr/_make_data.py
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

SEED = 0
rng = np.random.default_rng(SEED)

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

N_PROBES = 3000
N_CASE = 10
N_CTRL = 10
SAMPLES = [f"case_{i+1:02d}" for i in range(N_CASE)] + [f"ctrl_{i+1:02d}" for i in range(N_CTRL)]
GROUPS = (["case"] * N_CASE) + (["control"] * N_CTRL)

# ---------------------------------------------------------------------------
# 1) Background beta matrix: every probe gets a baseline mean in (0.05, 0.95)
#    with modest per-sample noise. Betas are bounded to (0, 1).
# ---------------------------------------------------------------------------
probe_ids = [f"cg{idx:07d}" for idx in range(1, N_PROBES + 1)]
base_mean = rng.uniform(0.05, 0.95, size=N_PROBES)
# The first 50 probes are the planted DM set (hyper in cases). Give them a
# LOW/moderate baseline so adding the +0.30 case effect stays well inside (0,1)
# with no ceiling clipping -> every planted probe shows a clean, large delta.
base_mean[:50] = rng.uniform(0.12, 0.40, size=50)
noise_sd = rng.uniform(0.015, 0.03, size=N_PROBES)   # gentle, realistic array noise

betas = np.empty((N_PROBES, N_CASE + N_CTRL), dtype=np.float64)
for p in range(N_PROBES):
    betas[p, :] = rng.normal(base_mean[p], noise_sd[p], size=N_CASE + N_CTRL)

# ---------------------------------------------------------------------------
# 2) Define 3 DMRs. Each DMR lives on its own chromosome near one gene and is
#    built from sub-clusters. Within a sub-cluster probes are < ~300 bp apart;
#    sub-clusters within a DMR are separated by ~2.5 kb (between 1 kb and 5 kb).
#
#    DMR A (gene GATA3,  chr10): 2 sub-clusters -> splits into 2 at 1 kb
#    DMR B (gene CDKN2A, chr9 ): 3 sub-clusters -> splits into 3 at 1 kb
#    DMR C (gene MLH1,   chr3 ): 2 sub-clusters -> splits into 2 at 1 kb
#    => 7 sub-clusters total. At 5 kb everything in a DMR merges -> 3 regions.
#
#    Total planted DM probes = 50.
# ---------------------------------------------------------------------------
# (gene, chrom, gene_start_pos, [sub-cluster sizes])  -> sizes sum per DMR
DMR_SPEC = [
    ("GATA3",  "chr10",  8_096_000, [13, 12]),       # 25 probes, 2 sub-clusters
    ("CDKN2A", "chr9",  21_968_000, [5, 5, 5]),      # 15 probes, 3 sub-clusters
    ("MLH1",   "chr3",  37_034_000, [5, 5]),         # 10 probes, 2 sub-clusters
]
assert sum(sum(s) for _, _, _, s in DMR_SPEC) == 50

WITHIN_CLUSTER_STEP = 120     # bp between adjacent probes inside a sub-cluster
SUBCLUSTER_GAP = 2_500        # bp gap between sub-clusters (1 kb < gap < 5 kb)
EFFECT = 0.30                 # mean beta increase in cases (hyper)

# assign the first 50 probe indices to DMRs (so the DM set is easy to define);
# the remaining 2950 are background.
dm_probe_idx = list(range(50))
bg_probe_idx = list(range(50, N_PROBES))

# manifest rows: probe -> chrom, pos, gene
manifest_rows = []

# --- background probes: scatter across the genome on assorted chromosomes ---
bg_chroms = [f"chr{c}" for c in list(range(1, 23)) + ["X"]]
for p in bg_probe_idx:
    ch = bg_chroms[p % len(bg_chroms)]
    pos = int(rng.integers(1_000_000, 240_000_000))
    manifest_rows.append((probe_ids[p], ch, pos, "."))   # "." = no nearby gene of interest

# --- DM probes: lay them down sub-cluster by sub-cluster, apply hyper effect --
dm_meta = []   # (probe_id, chrom, pos, gene, dmr_label, subcluster_label)
cursor = 0
for dmr_i, (gene, chrom, gstart, sub_sizes) in enumerate(DMR_SPEC):
    dmr_label = f"DMR_{chr(ord('A') + dmr_i)}"
    sub_start = gstart
    for sc_i, sc_n in enumerate(sub_sizes):
        sub_label = f"{dmr_label}.{sc_i+1}"
        pos = sub_start
        for _k in range(sc_n):
            p = dm_probe_idx[cursor]
            # planted hyper effect: raise the case half of the row by EFFECT
            betas[p, :N_CASE] = betas[p, :N_CASE] + EFFECT
            manifest_rows.append((probe_ids[p], chrom, pos, gene))
            dm_meta.append((probe_ids[p], chrom, pos, gene, dmr_label, sub_label))
            cursor += 1
            pos += WITHIN_CLUSTER_STEP
        # advance to next sub-cluster, leaving a gap that is between 1 kb and 5 kb
        sub_start = pos - WITHIN_CLUSTER_STEP + SUBCLUSTER_GAP

assert cursor == 50

# clip betas back into the valid (0,1) interval
betas = np.clip(betas, 1e-4, 1 - 1e-4)

# ---------------------------------------------------------------------------
# 3) Write betas.csv (probes x samples) and groups.csv
# ---------------------------------------------------------------------------
betas_df = pd.DataFrame(betas, index=probe_ids, columns=SAMPLES)
betas_df.index.name = "probe"
betas_df.round(5).to_csv(OUT / "betas.csv")

groups_df = pd.DataFrame({"sample": SAMPLES, "group": GROUPS})
groups_df.to_csv(OUT / "groups.csv", index=False)

# ---------------------------------------------------------------------------
# 4) manifest.csv  (probe -> chrom, pos, gene)
# ---------------------------------------------------------------------------
man = pd.DataFrame(manifest_rows, columns=["probe", "chrom", "pos", "gene"])
# sort by probe id for a tidy, stable file
man = man.sort_values("probe").reset_index(drop=True)
man.to_csv(OUT / "manifest.csv", index=False)

# ---------------------------------------------------------------------------
# 5) manifest_v2.csv  — re-annotation. A DEFINED subset of DM probes is remapped
#    to new positions AND new gene names. We remap the entire CDKN2A DMR (15
#    probes) to a different locus/gene (now near "MTAP" at chr9 ~21.80 Mb), and
#    additionally remap ONE sub-cluster of the GATA3 DMR (the 2nd sub-cluster,
#    12 probes) to a neighbouring gene "TAF3" at chr10 ~7.95 Mb. Everything else
#    is identical to v1.
#
#    => Genes that CHANGE for DM probes:  CDKN2A -> MTAP  (15 probes)
#                                         GATA3  -> TAF3  (12 probes, the .2 subcluster)
#    The first GATA3 sub-cluster (13 probes) and all of MLH1 stay put.
# ---------------------------------------------------------------------------
man_v2 = man.copy()
man_v2_map = {row[0]: list(row[1:]) for row in man.itertuples(index=False, name=None)}

# remap CDKN2A -> MTAP (new chrom region on chr9, new gene)
cdkn2a_probes = [m[0] for m in dm_meta if m[3] == "CDKN2A"]
mtap_start = 21_802_000
for j, pid in enumerate(sorted(cdkn2a_probes)):
    man_v2_map[pid] = ["chr9", mtap_start + j * WITHIN_CLUSTER_STEP, "MTAP"]

# remap GATA3 sub-cluster .2 -> TAF3
gata3_sc2_probes = [m[0] for m in dm_meta if m[5] == "DMR_A.2"]
taf3_start = 7_950_000
for j, pid in enumerate(sorted(gata3_sc2_probes)):
    man_v2_map[pid] = ["chr10", taf3_start + j * WITHIN_CLUSTER_STEP, "TAF3"]

man_v2 = pd.DataFrame(
    [(pid, *man_v2_map[pid]) for pid in man["probe"]],
    columns=["probe", "chrom", "pos", "gene"],
)
man_v2.to_csv(OUT / "manifest_v2.csv", index=False)

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
print("wrote:")
for f in ["betas.csv", "groups.csv", "manifest.csv", "manifest_v2.csv"]:
    fp = OUT / f
    print(f"  {f:18s} {fp.stat().st_size:>9,d} bytes")
print(f"betas shape: {betas_df.shape}  (probes x samples)")
print(f"planted DM probes: {len(dm_probe_idx)} (all hyper in cases, +{EFFECT} beta)")
print("DMRs (gene, chrom, n_probes, n_subclusters):")
for gene, chrom, _g, sub in DMR_SPEC:
    print(f"  {gene:7s} {chrom:6s} n={sum(sub):2d} subclusters={len(sub)}")
print("expected region counts:  3 (collapse within 5 kb)   7 (collapse within 1 kb)")
print("v2 changed gene assignments for DM probes:")
print(f"  CDKN2A -> MTAP : {len(cdkn2a_probes)} probes")
print(f"  GATA3  -> TAF3 : {len(gata3_sc2_probes)} probes (sub-cluster DMR_A.2)")
print("  (GATA3 sub-cluster .1 [13 probes] and MLH1 [10 probes] unchanged)")
