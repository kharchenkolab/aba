#!/usr/bin/env python
"""Deterministic generator for the `colocalization` scenario.

Builds 4 two-channel fluorescence fields (512x512, uint16) under data/coloc/:
  f1_ch1.tif f1_ch2.tif  ... f4_ch1.tif f4_ch2.tif

Within synthetic "cells" (foreground blobs), channel 2 is a tuned mixture of
channel 1's signal plus independent signal, so the two markers colocalize at a
PLANTED level (Pearson ~0.6, Manders M1 ~0.7) in the three GOOD fields
(f1, f2, f4). Field 3 (f3) is the deliberate OUTLIER: it is acquired
"out of focus" (heavy Gaussian blur) and "over-exposed" (saturation/clipping),
which collapses its texture and inflates/distorts its coefficients so it stands
out as the bad field that should be excluded.

Everything is seeded (seed=0 + per-field deterministic seeds) so the planted
truth is stable. Run with the scenario venv:
  "" _make_data.py
"""

import os
import numpy as np
from scipy.ndimage import gaussian_filter
import tifffile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "coloc")

H = W = 384
N_CELLS = 11          # blobs per field
CELL_R = 34           # nominal cell radius (px)
UINT16_MAX = 65535
TARGET_R = 0.82       # latent texture correlation (within-mask masked Pearson
                      # comes out ~0.6 after ch2 "hole" patches are applied)


def _cell_mask(rng):
    """Union of N_CELLS soft Gaussian blobs -> binary foreground mask + a
    smooth foreground intensity envelope (so cells are brighter in the middle)."""
    env = np.zeros((H, W), dtype=np.float64)
    yy, xx = np.mgrid[0:H, 0:W]
    margin = CELL_R + 8
    for _ in range(N_CELLS):
        cy = rng.integers(margin, H - margin)
        cx = rng.integers(margin, W - margin)
        r = CELL_R * rng.uniform(0.75, 1.2)
        d2 = (yy - cy) ** 2 + (xx - cx) ** 2
        env += np.exp(-d2 / (2.0 * (r * 0.55) ** 2))
    env = np.clip(env, 0, 1.0)
    mask = env > 0.18
    return mask, env


def _texture(rng):
    """A zero-mean, unit-variance spatially-smooth texture field.

    sigma=3.0 keeps many independent texture "grains" per cell, which makes the
    within-mask correlation statistics stable across fields (tight planted r)."""
    t = gaussian_filter(rng.standard_normal((H, W)), sigma=3.0)
    t = (t - t.mean()) / (t.std() + 1e-12)
    return t


def make_good_field(seed, target_r=TARGET_R):
    """A GOOD field. The two channels share a tunable fraction of their
    foreground texture so the WITHIN-MASK Pearson lands near `target_r` and
    Manders M1 ~0.7. Background outside cells is low and uncorrelated.

    Construction (within the cell mask): standardized correlated textures
        ch1_tex = X
        ch2_tex = r*X + sqrt(1-r^2)*Y
    with X, Y independent unit-variance fields. A gentle envelope makes cell
    centers brighter; a low positive floor keeps both channels non-negative
    (like a fluorescence signal above background)."""
    rng = np.random.default_rng(seed)
    mask, env = _cell_mask(rng)

    X = _texture(rng)
    Y = _texture(rng)
    r = target_r
    ch1_tex = X
    ch2_tex = r * X + np.sqrt(1.0 - r * r) * Y

    # gentle multiplicative envelope (kept small so texture, not envelope,
    # dominates the within-mask variance -> Pearson stays near target_r).
    # ch2 also gets real "holes" (sub-threshold regions) via a coarse mask, so
    # the Manders M1 (fraction of ch1 coinciding with ch2-positive pixels) lands
    # near ~0.7 instead of ~1 (which is what you get if both fill the cells).
    gain = 0.18 + 0.12 * env
    floor = 0.30  # signal floor (well below full-scale -> good fields don't clip)

    # coarse low-frequency field -> ch2 "absent" patches inside cells
    holes = gaussian_filter(rng.standard_normal((H, W)), sigma=9.0)
    holes = (holes - holes.mean()) / (holes.std() + 1e-12)
    ch2_present = holes > -0.52          # ~70% of cell area has ch2 signal

    ch1 = np.zeros((H, W))
    ch2 = np.zeros((H, W))
    ch1[mask] = floor + gain[mask] * ch1_tex[mask]
    c2 = floor + gain * ch2_tex
    c2[~ch2_present] = 0.0               # ch2 dark in the "hole" patches
    ch2[mask] = c2[mask]

    # low, independent background + read noise everywhere
    bg = 0.04
    ch1 = ch1 + bg + 0.015 * rng.standard_normal((H, W))
    ch2 = ch2 + bg + 0.015 * rng.standard_normal((H, W))
    ch1 = np.clip(ch1, 0, None)
    ch2 = np.clip(ch2, 0, None)

    # tiny PSF blur (in focus)
    ch1 = gaussian_filter(ch1, sigma=0.8)
    ch2 = gaussian_filter(ch2, sigma=0.8)
    return ch1, ch2, mask


def make_bad_field(seed):
    """The OUTLIER field (f3): out of focus + saturated.

    Start from a good-field construction, then (a) blur heavily so the
    foreground texture smears across the whole frame, and (b) push the gain so
    a large fraction of foreground pixels clip at the sensor max. Both effects
    distort the colocalization estimate and make this field a clear outlier."""
    ch1, ch2, mask = make_good_field(seed)

    # (a) out-of-focus: heavy isotropic blur (smears texture across the frame;
    #     the blur correlates neighbouring cross-channel pixels and inflates r)
    ch1 = gaussian_filter(ch1, sigma=12.0)
    ch2 = gaussian_filter(ch2, sigma=12.0)

    # (b) over-exposure: high gain + hard clip -> large saturation plateaus
    #     (both channels pinned at the sensor max over big regions -> distorts
    #     Pearson and Manders; ~half the cell area clips)
    ch1 = ch1 * 6.0
    ch2 = ch2 * 6.0
    return ch1, ch2, mask


def to_uint16(x):
    """Scale a float field to uint16 with a fixed full-scale so saturation in
    the bad field is preserved (values >= 1.0 clip to the sensor max)."""
    y = np.clip(x, 0.0, 1.0)
    return (y * UINT16_MAX).round().astype(np.uint16)


def main():
    os.makedirs(OUT, exist_ok=True)
    # Master seed 0; per-field seeds derived deterministically.
    master = np.random.default_rng(0)
    field_seeds = [int(s) for s in master.integers(1, 10_000, size=4)]

    truths = {}
    for i, fseed in enumerate(field_seeds, start=1):
        if i == 3:
            ch1f, ch2f, mask = make_bad_field(fseed)
        else:
            ch1f, ch2f, mask = make_good_field(fseed)

        ch1 = to_uint16(ch1f)
        ch2 = to_uint16(ch2f)
        tifffile.imwrite(os.path.join(OUT, f"f{i}_ch1.tif"), ch1, compression="deflate")
        tifffile.imwrite(os.path.join(OUT, f"f{i}_ch2.tif"), ch2, compression="deflate")

        # --- report planted coloc within the (ground-truth) cell mask ---
        a = ch1[mask].astype(np.float64)
        b = ch2[mask].astype(np.float64)
        pear = np.corrcoef(a, b)[0, 1]
        # Manders M1/M2 with a background threshold = mean intensity in the
        # OUTSIDE-cell background (a robust, channel-specific cutoff).
        ta = float(ch1[~mask].mean()) + 3.0 * float(ch1[~mask].std())
        tb = float(ch2[~mask].mean()) + 3.0 * float(ch2[~mask].std())
        m1 = (a * (b > tb)).sum() / (a.sum() + 1e-12)
        m2 = (b * (a > ta)).sum() / (b.sum() + 1e-12)
        sat_frac = float((ch1 >= UINT16_MAX).mean())

        # --- ALSO report the agent-likely "in-cell" estimate: Otsu per-channel
        #     threshold, Pearson over the INTERSECTION (both markers present),
        #     which is the most defensible answer to "within the cells". ---
        from skimage.filters import threshold_otsu
        c1n = ch1.astype(np.float64) / UINT16_MAX
        c2n = ch2.astype(np.float64) / UINT16_MAX
        t1 = threshold_otsu(c1n)
        t2 = threshold_otsu(c2n)
        inter = (c1n > t1) & (c2n > t2)
        union = (c1n > t1) | (c2n > t2)
        pear_inter = np.corrcoef(c1n[inter], c2n[inter])[0, 1] if inter.sum() > 10 else 0.0
        fg_frac = float(union.mean())

        truths[f"f{i}"] = dict(
            pearson_masked=round(float(pear), 3),
            pearson_intersection=round(float(pear_inter), 3),
            manders_m1=round(float(m1), 3),
            manders_m2=round(float(m2), 3),
            sat_frac_ch1=round(sat_frac, 4),
            fg_frac=round(fg_frac, 3),
            bad=(i == 3),
        )

    # summary over the GOOD fields
    good = [truths[k] for k in ("f1", "f2", "f4")]
    print("Per-field planted colocalization:")
    print(f"  {'field':5} {'Pear(GTmask)':>12} {'Pear(inter)':>11} {'M1':>6} {'M2':>6} "
          f"{'sat_ch1':>8} {'fg_frac':>7}")
    for k in ("f1", "f2", "f3", "f4"):
        t = truths[k]
        tag = "  <-- OUTLIER (out of focus + saturated)" if t["bad"] else ""
        print(f"  {k:5} {t['pearson_masked']:12.3f} {t['pearson_intersection']:11.3f} "
              f"{t['manders_m1']:6.3f} {t['manders_m2']:6.3f} {t['sat_frac_ch1']:8.4f} "
              f"{t['fg_frac']:7.3f}{tag}")
    gp = np.mean([t["pearson_masked"] for t in good])
    gpi = np.mean([t["pearson_intersection"] for t in good])
    gm1 = np.mean([t["manders_m1"] for t in good])
    print(f"GOOD-field mean: Pearson(GTmask)={gp:.3f}  Pearson(inter)={gpi:.3f}  M1={gm1:.3f}")

    # total bytes
    total = 0
    for fn in os.listdir(OUT):
        total += os.path.getsize(os.path.join(OUT, fn))
    print(f"\nWrote {len(os.listdir(OUT))} files to {OUT}")
    print(f"Total bytes: {total} ({total/1024:.1f} KiB)")


if __name__ == "__main__":
    main()
