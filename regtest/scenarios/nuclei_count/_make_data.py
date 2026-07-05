#!/usr/bin/env python
"""Deterministic generator for the `nuclei_count` bioimaging scenario.

Builds a synthetic 2-channel fluorescence field (512x512):

  * nuclei_dapi.tif   -- a DAPI-like nuclear channel: EXACTLY 42 nuclei of
                         varied size/intensity on a noisy autofluorescence
                         background. SIX of the 42 are arranged as touching
                         pairs (3 pairs) -- wait, see below -- placed so close
                         that a single Otsu threshold fuses each pair into one
                         blob; only a distance-transform watershed (or a
                         learning-based segmenter) splits them back into two.
  * nuclei_marker.tif -- a second (marker) channel: a known subset of nuclei
                         is "marker-positive", i.e. carries marker signal well
                         above the planted positivity threshold; the rest are
                         marker-negative (background only).

Ground truth (seed=0, fully deterministic) is written next to the TIFFs as
`ground_truth.csv` for the human judge -- the agent never sees it; the prompt
only references the two TIFFs.

PLANTED TRUTH
  * 42 nuclei total.
  * 6 of them participate in 3 touching PAIRS (so a naive, no-watershed count
    sees 39 objects: 36 isolated + 3 fused pairs). Watershed -> 42.
  * 15 nuclei are marker-positive (marker mean intensity above the planted
    threshold); 27 are marker-negative.
  * A handful (4) of small debris specks are sprinkled into the DAPI channel
    BELOW the real-nucleus size range, so a size filter is needed to avoid
    over-counting; these are NOT part of the 42.

Run with the scenario venv:
  "" _make_data.py
"""

import os
import numpy as np
from skimage.draw import disk
from skimage.filters import gaussian
import tifffile

SEED = 0
SIZE = 512
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# Planted constants (the checkable truth).
N_NUCLEI = 42
N_PAIRS = 3            # 3 touching pairs -> 6 nuclei involved in merges
N_MARKER_POS = 15
N_DEBRIS = 4
MARKER_POS_THRESHOLD = 0.18   # marker-channel mean-intensity cutoff (planted)


def _nonoverlapping_centers(rng, n, rmin, rmax, existing, min_gap):
    """Place n centers with radii so that disks stay >= min_gap apart from each
    other and from `existing` (list of (y, x, r)). Returns list of (y, x, r)."""
    placed = []
    margin = rmax + 4
    tries = 0
    while len(placed) < n and tries < 200000:
        tries += 1
        r = float(rng.uniform(rmin, rmax))
        y = float(rng.uniform(margin, SIZE - margin))
        x = float(rng.uniform(margin, SIZE - margin))
        ok = True
        for (yy, xx, rr) in placed + existing:
            if np.hypot(y - yy, x - xx) < (r + rr + min_gap):
                ok = False
                break
        if ok:
            placed.append((y, x, r))
    if len(placed) < n:
        raise RuntimeError(f"could only place {len(placed)}/{n} centers")
    return placed


def main():
    rng = np.random.default_rng(SEED)

    dapi = np.zeros((SIZE, SIZE), dtype=np.float32)
    marker = np.zeros((SIZE, SIZE), dtype=np.float32)

    truth_rows = []   # (id, y, x, radius, area_px, dapi_peak, marker_pos)
    nuc_id = 0

    # ---- 1. Touching pairs (placed first so we control their geometry) ----
    # Each pair: two nuclei whose centers are < r1 + r2 apart, so their disks
    # physically overlap and a single threshold yields one fused blob.
    pair_anchor = []  # keep anchors well separated from each other + later nuclei
    anchors = _nonoverlapping_centers(
        rng, N_PAIRS, rmin=12, rmax=15, existing=[], min_gap=60
    )
    isolated_existing = []  # (y, x, r) all placed nuclei, to keep singles apart
    for (cy, cx, _r) in anchors:
        r1 = float(rng.uniform(10, 13))
        r2 = float(rng.uniform(10, 13))
        # center-to-center distance: overlapping but not concentric.
        sep = 0.72 * (r1 + r2)      # < r1 + r2  -> disks overlap -> fuse
        ang = float(rng.uniform(0, 2 * np.pi))
        dy, dx = np.sin(ang), np.cos(ang)
        y1, x1 = cy - 0.5 * sep * dy, cx - 0.5 * sep * dx
        y2, x2 = cy + 0.5 * sep * dy, cx + 0.5 * sep * dx
        for (yy, xx, rr) in [(y1, x1, r1), (y2, x2, r2)]:
            peak = float(rng.uniform(0.55, 0.95))
            rr_idx, cc_idx = disk((yy, xx), rr, shape=dapi.shape)
            dapi[rr_idx, cc_idx] += peak
            area = int(len(rr_idx))
            truth_rows.append([nuc_id, round(yy, 2), round(xx, 2),
                               round(rr, 2), area, round(peak, 3), False, True])
            isolated_existing.append((yy, xx, rr))
            nuc_id += 1

    # ---- 2. Isolated nuclei (the remaining 42 - 2*N_PAIRS) ----
    n_iso = N_NUCLEI - 2 * N_PAIRS
    iso = _nonoverlapping_centers(
        rng, n_iso, rmin=7, rmax=16, existing=isolated_existing, min_gap=10
    )
    for (yy, xx, rr) in iso:
        peak = float(rng.uniform(0.45, 0.95))
        rr_idx, cc_idx = disk((yy, xx), rr, shape=dapi.shape)
        dapi[rr_idx, cc_idx] += peak
        area = int(len(rr_idx))
        truth_rows.append([nuc_id, round(yy, 2), round(xx, 2),
                           round(rr, 2), area, round(peak, 3), False, False])
        isolated_existing.append((yy, xx, rr))
        nuc_id += 1

    assert nuc_id == N_NUCLEI, nuc_id

    # ---- 3. Choose 15 marker-positive nuclei (mix of paired + isolated) ----
    pos_idx = set(rng.choice(N_NUCLEI, size=N_MARKER_POS, replace=False).tolist())
    for row in truth_rows:
        i = row[0]
        if i in pos_idx:
            row[6] = True
            yy, xx, rr = row[1], row[2], row[3]
            # marker signal clearly above MARKER_POS_THRESHOLD.
            msig = float(rng.uniform(0.45, 0.85))
            rr_idx, cc_idx = disk((yy, xx), rr, shape=marker.shape)
            marker[rr_idx, cc_idx] += msig
        else:
            # marker-negative: faint sub-threshold signal only.
            yy, xx, rr = row[1], row[2], row[3]
            msig = float(rng.uniform(0.02, 0.07))
            rr_idx, cc_idx = disk((yy, xx), rr, shape=marker.shape)
            marker[rr_idx, cc_idx] += msig

    # ---- 4. Debris specks in DAPI (below real nucleus size; not counted) ----
    debris = _nonoverlapping_centers(
        rng, N_DEBRIS, rmin=2.0, rmax=3.0, existing=isolated_existing, min_gap=10
    )
    for (yy, xx, rr) in debris:
        rr_idx, cc_idx = disk((yy, xx), rr, shape=dapi.shape)
        dapi[rr_idx, cc_idx] += float(rng.uniform(0.5, 0.8))

    # ---- 5. Realistic optics + noise ----
    # Slight blur (point spread function), low-frequency autofluorescence
    # background, and Poisson-ish + Gaussian read noise. Keep the signal well
    # above noise so the truth stays recoverable.
    dapi = gaussian(dapi, sigma=1.2, preserve_range=True)
    marker = gaussian(marker, sigma=1.2, preserve_range=True)

    # smooth background gradient
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float32)
    bg = 0.05 + 0.04 * (yy / SIZE) + 0.03 * np.sin(xx / 90.0)
    dapi = dapi + bg
    marker = marker + 0.04 + 0.02 * (xx / SIZE)

    dapi = dapi + rng.normal(0, 0.02, dapi.shape).astype(np.float32)
    marker = marker + rng.normal(0, 0.015, marker.shape).astype(np.float32)
    dapi = np.clip(dapi, 0, None)
    marker = np.clip(marker, 0, None)

    # ---- 6. Quantize to 16-bit and save ----
    def to_u16(a):
        a = a / max(a.max(), 1e-6)
        return (a * 60000.0).astype(np.uint16)

    dapi_u16 = to_u16(dapi)
    marker_u16 = to_u16(marker)

    os.makedirs(DATA, exist_ok=True)
    # Lossless deflate compression keeps exact pixel values while staying small.
    tifffile.imwrite(os.path.join(DATA, "nuclei_dapi.tif"), dapi_u16,
                     compression="zlib")
    tifffile.imwrite(os.path.join(DATA, "nuclei_marker.tif"), marker_u16,
                     compression="zlib")

    # ---- 7. Ground-truth table (for the judge, NOT referenced by prompts) ----
    import csv
    gt_path = os.path.join(DATA, "ground_truth.csv")
    with open(gt_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["nucleus_id", "centroid_y", "centroid_x", "radius_px",
                    "area_px", "dapi_peak", "marker_positive", "in_touching_pair"])
        for r in truth_rows:
            w.writerow(r)

    n_pos = sum(1 for r in truth_rows if r[6])
    n_pair = sum(1 for r in truth_rows if r[7])
    print(f"nuclei_dapi.tif   {os.path.getsize(os.path.join(DATA,'nuclei_dapi.tif')):>8} bytes")
    print(f"nuclei_marker.tif {os.path.getsize(os.path.join(DATA,'nuclei_marker.tif')):>8} bytes")
    print(f"ground_truth.csv  {os.path.getsize(gt_path):>8} bytes")
    print(f"nuclei={nuc_id}  marker_positive={n_pos}  in_touching_pairs={n_pair} "
          f"({N_PAIRS} pairs)  debris={N_DEBRIS}  marker_threshold={MARKER_POS_THRESHOLD}")


if __name__ == "__main__":
    main()
