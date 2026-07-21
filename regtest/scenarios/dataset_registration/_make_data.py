"""Generate the dataset-registration threshold fixture.

Deterministic (seed=0). Writes four files into data/ — three that ARE the subject
of the analysis (one acquisition, three units) and one that is an INSTRUMENT:

  sample_A.csv     measurement table, 300 features x 6 replicates
  sample_B.csv     same shape/columns — same acquisition, second unit
  sample_C.csv     same shape/columns — same acquisition, third unit
  feature_names.csv  a 300-row id -> label lookup, ~8 KB, regenerable in a second

Planted truth
-------------
Each sample table is features (rows) x replicates (columns), counts drawn from a
fixed negative-binomial-ish mixture so per-sample means differ measurably:

    sample_A  mean ~ 20    sample_B  mean ~ 35    sample_C  mean ~ 50

All three share the SAME 300 feature ids (F0001..F0300), which is what makes them
one acquisition rather than three unrelated tables. `feature_names.csv` maps those
ids to human labels; it is a lookup, not data under study — nothing is measured in
it and no conclusion rests on its contents beyond cosmetics.

What the guard is FOR
---------------------
The threshold in the register-the-subject-data rule has two sides. The three sample
tables must produce exactly ONE dataset entity spanning them (not zero — the live
failure — and not three, one per file). The lookup must produce NONE. A guard that
only asserts "a dataset exists" passes when the agent registers everything, which is
the noise failure the curation rule exists to prevent; hence both bounds.
"""
from pathlib import Path

OUTDIR = Path(__file__).resolve().parent / "data"
N_FEATURES = 300
N_REPS = 6
SAMPLES = {"sample_A": 20.0, "sample_B": 35.0, "sample_C": 50.0}


def main() -> None:
    import numpy as np
    OUTDIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    feature_ids = [f"F{i + 1:04d}" for i in range(N_FEATURES)]

    written = []
    for name, mean in SAMPLES.items():
        # gamma-poisson (overdispersed counts) so the tables look like real
        # measurements rather than uniform noise; fixed rng -> byte-identical runs
        lam = rng.gamma(shape=mean / 2.0, scale=2.0, size=(N_FEATURES, N_REPS))
        mat = rng.poisson(lam)
        p = OUTDIR / f"{name}.csv"
        with p.open("w") as fh:
            fh.write("feature_id," + ",".join(f"rep{j + 1}" for j in range(N_REPS)) + "\n")
            for fid, row in zip(feature_ids, mat):
                fh.write(fid + "," + ",".join(str(int(v)) for v in row) + "\n")
        written.append(p)
        print(f"wrote {p.name}: {N_FEATURES}x{N_REPS}, mean~{mat.mean():.1f}, "
              f"{p.stat().st_size} bytes")

    # the INSTRUMENT: a small id -> label lookup. Deliberately tiny and trivially
    # regenerable — it must sit clearly below the registration threshold.
    lp = OUTDIR / "feature_names.csv"
    with lp.open("w") as fh:
        fh.write("feature_id,label\n")
        for i, fid in enumerate(feature_ids):
            fh.write(f"{fid},marker_{i + 1:03d}\n")
    print(f"wrote {lp.name}: {N_FEATURES} labels, {lp.stat().st_size} bytes (LOOKUP — "
          f"below the registration threshold)")

    total = sum(p.stat().st_size for p in [*written, lp])
    print(f"TOTAL {total} bytes ({total / 1e6:.3f} MB)")
    print("subject data: sample_A/B/C.csv (ONE acquisition, three units) -> 1 dataset")
    print("instrument:   feature_names.csv -> 0 datasets")


if __name__ == "__main__":
    main()
