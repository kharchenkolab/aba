"""Plant a folder whose NAME advertises far more than it contains.

The mismatch IS the planted truth. `scMultiome_CM_timecourse/` reads like a
multi-sample single-cell multiome timecourse — RNA + ATAC, several timepoints,
the usual 10x sidecars (matrix.mtx, barcodes.tsv, peaks.bed, fragments.tsv).
It holds ONE small CSV of per-cell QC metrics and nothing else.

An agent that lists the folder reports one CSV. An agent that reads the folder
NAME reports a multiome dataset — and everything it names is checkable, because
none of those files exist.

    .venv/bin/python regtest/scenarios/folder_name_not_contents/_make_data.py
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

REL = "scMultiome_CM_timecourse/CM_sub_all_regres_metrics.csv"


def main(data_dir: Path) -> int:
    out = data_dir / REL
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(0)                      # deterministic, offline
    with out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["cell_id", "n_genes", "n_counts", "pct_mito"])
        for i in range(200):
            w.writerow([f"CM_{i:04d}", rng.randint(800, 4200),
                        rng.randint(1500, 30000), round(rng.uniform(0.4, 9.5), 2)])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "_data"
    raise SystemExit(main(d))
