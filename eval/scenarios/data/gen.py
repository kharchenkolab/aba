"""Deterministic synthetic data for the eval scenarios.

Run:  python eval/scenarios/data/gen.py
Produces the CSVs in this directory (gitignored — regenerate any time).
Planted ground truth is documented in eval/scenarios/scenarios.md and verified
by the printout at the end of this script.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from pathlib import Path

OUT = Path(__file__).resolve().parent
rng = np.random.default_rng(7)
ISG = ["IFIT1", "IFIT3", "ISG15", "MX1", "OAS1"]


def noise(mean, n, sd=0.4):
    return np.clip(rng.normal(mean, sd, n), 0, None)


# ---------------------------------------------------------------- primary
def gen_monocyte_stim() -> pd.DataFrame:
    donors = ["D1", "D2", "D3", "D4", "D5", "D6"]
    times = ["0h", "2h", "6h"]
    CELLS = 15
    # ISG module induction strength per donor: strong in D1/D2/D4/D5, WEAK in D3
    # (LOO-sensitive), noisy in D6.
    mod = {"D1": 1.0, "D2": 1.1, "D3": 0.25, "D4": 1.0, "D5": 0.9, "D6": 0.6}
    # CXCL10 induction concentrated in D3 → removing D3 collapses CXCL10 while
    # the module (other ISGs) still holds.
    cxcl = {"D1": 0.3, "D2": 0.3, "D3": 2.2, "D4": 0.3, "D5": 0.3, "D6": 0.4}
    tf = {"0h": 0.0, "2h": 0.6, "6h": 1.0}
    rows, cid = [], 0
    for d in donors:
        bad = d == "D6"                                   # low-quality donor
        for c in ["control", "stim"]:
            stim = c == "stim"
            for t in times:
                for _ in range(CELLS):
                    cid += 1
                    mt = float(noise(0.13 if bad else 0.04, 1, 0.02)[0])
                    ng = int(np.clip(rng.normal(1100 if bad else 2100, 250, 1), 300, None)[0])
                    nc = int(ng * float(rng.normal(2.4, 0.2)))
                    eff = (3.0 if stim else 0.0) * mod[d] * tf[t]      # module induction
                    isg = {g: float(noise(1.0, 1)[0]) + eff + float(rng.normal(0, 0.4)) for g in ISG}
                    cxcl10 = float(noise(1.0, 1)[0]) + (2.5 * cxcl[d] * tf[t] if stim else 0.0)
                    # GENE_X: looks stim-induced but ONLY through the contaminated
                    # donor D6 (artifact, correlates with mt_fraction).
                    gene_x = float(noise(1.0, 1)[0]) + (3.0 if (stim and bad) else 0.0) + (mt * 4 if bad else 0.0)
                    rows.append(dict(
                        cell_id=f"c{cid:04d}", donor=d, condition=c, timepoint=t,
                        n_genes=ng, n_counts=nc, mt_fraction=round(mt, 4),
                        **{g: round(isg[g], 3) for g in ISG},
                        CXCL10=round(cxcl10, 3),
                        ACTB=round(float(noise(5.0, 1, 0.3)[0]), 3),    # housekeeping (flat)
                        GAPDH=round(float(noise(5.0, 1, 0.3)[0]), 3),
                        GENE_X=round(gene_x, 3),
                    ))
    return pd.DataFrame(rows)


# ------------------------------------------------------------ replication
def gen_monocyte_ref() -> pd.DataFrame:
    donors = ["R1", "R2", "R3", "R4"]
    CELLS = 30
    rows, cid = [], 0
    for d in donors:
        for c in ["control", "stim"]:
            stim = c == "stim"
            for _ in range(CELLS):
                cid += 1
                mt = float(noise(0.045, 1, 0.02)[0])              # all clean
                ng = int(np.clip(rng.normal(2050, 250, 1), 300, None)[0])
                eff = 2.8 if stim else 0.0                         # robust in ALL donors
                isg = {g: float(noise(1.0, 1)[0]) + eff + float(rng.normal(0, 0.4)) for g in ISG}
                rows.append(dict(
                    cell_id=f"r{cid:04d}", donor=d, condition=c, timepoint="6h",
                    n_genes=ng, n_counts=int(ng * 2.4), mt_fraction=round(mt, 4),
                    **{g: round(isg[g], 3) for g in ISG},
                    CXCL10=round(float(noise(1.0, 1)[0]) + (0.9 if stim else 0.0), 3),
                    ACTB=round(float(noise(5.0, 1, 0.3)[0]), 3),
                    GAPDH=round(float(noise(5.0, 1, 0.3)[0]), 3),
                    GENE_X=round(float(noise(1.0, 1)[0]), 3),       # FLAT (confirms artifact)
                ))
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ stubs
def gen_stub(prefix, donors, marker, marker_mean=3.0) -> pd.DataFrame:
    rows, cid = [], 0
    for d in donors:
        for c in ["control", "stim"]:
            for _ in range(20):
                cid += 1
                rows.append(dict(
                    cell_id=f"{prefix}{cid:03d}", donor=d, condition=c,
                    n_genes=int(np.clip(rng.normal(2000, 250, 1), 300, None)[0]),
                    n_counts=int(rng.normal(4800, 600)),
                    mt_fraction=round(float(noise(0.04, 1, 0.015)[0]), 4),
                    **{marker: round(float(noise(marker_mean, 1, 0.5)[0]), 3)},
                ))
    return pd.DataFrame(rows)


def verify(stim: pd.DataFrame, ref: pd.DataFrame):
    print("\n=== planted ground-truth checks ===")
    mt = stim.groupby("donor")["mt_fraction"].mean()
    print("mt_fraction by donor (D6 should be high):")
    print(mt.round(3).to_string())
    isg_score = stim[ISG].mean(axis=1)
    s = stim.assign(isg=isg_score)
    by_ct = s.groupby("condition")["isg"].mean()
    print(f"\nISG module: control={by_ct['control']:.2f} stim={by_ct['stim']:.2f} (stim higher)")
    by_t = s[s.condition == "stim"].groupby("timepoint")["isg"].mean()
    print(f"ISG by timepoint (stim): 0h={by_t['0h']:.2f} 2h={by_t['2h']:.2f} 6h={by_t['6h']:.2f} (peak 6h)")
    # module robustness: stim-control ISG delta per donor
    delta = (s[s.condition == "stim"].groupby("donor")["isg"].mean()
             - s[s.condition == "control"].groupby("donor")["isg"].mean())
    print("\nISG stim−control delta by donor (D3 weakest → 5/6 robust):")
    print(delta.round(2).to_string())
    # CXCL10 fragility: stim-control with vs without D3
    def cx_delta(df):
        g = df.groupby("condition")["CXCL10"].mean()
        return g["stim"] - g["control"]
    print(f"\nCXCL10 stim−control: all={cx_delta(stim):.2f}  drop-D3={cx_delta(stim[stim.donor!='D3']):.2f} (collapses without D3)")
    # GENE_X artifact: stim-control with vs without D6
    def gx_delta(df):
        g = df.groupby("condition")["GENE_X"].mean()
        return g["stim"] - g["control"]
    print(f"GENE_X stim−control: all={gx_delta(stim):.2f}  drop-D6={gx_delta(stim[stim.donor!='D6']):.2f} (artifact, vanishes without D6)")
    rg = ref.groupby("condition")[ISG].mean().mean(axis=1)
    print(f"\nREF ISG module: control={rg['control']:.2f} stim={rg['stim']:.2f} (replicates)")
    print(f"REF GENE_X stim−control: {gx_delta(ref):.2f} (flat — confirms artifact)\n")


if __name__ == "__main__":
    stim = gen_monocyte_stim();  stim.to_csv(OUT / "monocyte_stim.csv", index=False)
    ref = gen_monocyte_ref();    ref.to_csv(OUT / "monocyte_ref.csv", index=False)
    gen_stub("t", ["T1", "T2"], "CD3D").to_csv(OUT / "tcell_stim.csv", index=False)
    gen_stub("b", ["B1", "B2"], "MS4A1").to_csv(OUT / "bcell_baseline.csv", index=False)
    for f in ["monocyte_stim.csv", "monocyte_ref.csv", "tcell_stim.csv", "bcell_baseline.csv"]:
        p = OUT / f
        print(f"  {f:24s} {len(pd.read_csv(p)):4d} rows  {p.stat().st_size/1024:.1f} KB")
    verify(stim, ref)
