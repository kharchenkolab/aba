"""Make a tiny DNA-damage foci imaging dataset for the foci_count scenario.

Two conditions, each a 2-channel widefield-style field of view saved as separate
single-channel 16-bit TIFFs:

    data/foci/ctrl_dapi.tif      nuclear channel,  control
    data/foci/ctrl_foci.tif      foci channel,     control   (~2 foci / nucleus)
    data/foci/treated_dapi.tif   nuclear channel,  treated
    data/foci/treated_foci.tif   foci channel,     treated   (~12 foci / nucleus)

Each nucleus is a smooth blurred disk in the DAPI channel. Inside each nucleus
we plant an EXACT number of foci (small Gaussian blobs) in the foci channel.
Foci come in two brightness tiers:
  - bright foci: easy to detect
  - faint foci : real, but dim enough that a default/too-conservative detector
                 misses them (motivates the "tune sensitivity" revise step)
We also sprinkle a few bright background speckles (camera hot-pixel-like noise)
PLUS low-amplitude Poisson/Gaussian noise everywhere, so a too-sensitive
detector over-counts. The correctly-tuned detector recovers exactly the planted
per-nucleus counts.

Ground truth (per-nucleus foci counts + nuclei counts + speckle counts) is
written to data/foci/ground_truth.json for checking, and printed.

Deterministic: fixed seed. Total output kept well under 2 MB.

    tools/scenario-venv/bin/python regtest/scenarios/foci_count/_make_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import tifffile

SEED = 0
rng = np.random.default_rng(SEED)

OUT_DIR = Path(__file__).resolve().parent / "data" / "foci"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ---- field-of-view geometry -------------------------------------------------
H = W = 440                      # small image; <2 MB total across 4 uint16 TIFFs
NUC_RADIUS = 34                  # nucleus radius (px) -- big enough to resolve ~12 foci
NUC_MARGIN = NUC_RADIUS + 8      # keep nuclei away from borders
GRID = 4                         # 4x4 candidate grid of nucleus slots
MIN_FOCUS_SEP = 7.0              # min center-to-center separation between planted foci (px)

# intensities (16-bit range)
DAPI_PEAK = 9000                 # nucleus brightness in DAPI channel
DAPI_BG = 300                    # DAPI background
FOCI_BG = 200                    # foci channel background
FOCI_BRIGHT = 5200               # bright focus peak amplitude over background
FOCI_FAINT = 1500                # faint (real) focus peak amplitude over background
SPECKLE_AMP = 4800               # bright background speckle amplitude (noise, not a focus)
FOCUS_SIGMA = 1.6                # focus blob sigma (px) -> small spots
SPECKLE_SIGMA = 0.7              # speckles are tighter/sharper than real foci

# noise
DAPI_NOISE = 60                  # gaussian read noise, DAPI
FOCI_NOISE = 90                  # gaussian read noise, foci channel (enough to tempt over-counting)


def _add_blob(img: np.ndarray, cy: float, cx: float, amp: float, sigma: float) -> None:
    """Add a Gaussian blob centered at (cy, cx) into img in-place."""
    r = int(np.ceil(sigma * 4))
    y0, y1 = max(0, int(cy) - r), min(img.shape[0], int(cy) + r + 1)
    x0, x1 = max(0, int(cx) - r), min(img.shape[1], int(cx) + r + 1)
    ys = np.arange(y0, y1)[:, None]
    xs = np.arange(x0, x1)[None, :]
    g = np.exp(-(((ys - cy) ** 2 + (xs - cx) ** 2) / (2.0 * sigma ** 2)))
    img[y0:y1, x0:x1] += amp * g


def _nucleus_centers() -> list[tuple[int, int]]:
    """Pick non-overlapping nucleus centers on a jittered grid (deterministic)."""
    step_y = (H - 2 * NUC_MARGIN) / (GRID - 1)
    step_x = (W - 2 * NUC_MARGIN) / (GRID - 1)
    centers = []
    for iy in range(GRID):
        for ix in range(GRID):
            cy = NUC_MARGIN + iy * step_y + rng.integers(-4, 5)
            cx = NUC_MARGIN + ix * step_x + rng.integers(-4, 5)
            centers.append((int(cy), int(cx)))
    return centers


def _make_condition(name: str, n_nuclei: int, mean_foci: int) -> dict:
    """Build a 2-channel condition; plant exact foci counts per nucleus."""
    dapi = np.full((H, W), float(DAPI_BG))
    foci = np.full((H, W), float(FOCI_BG))

    centers = _nucleus_centers()[:n_nuclei]

    yy, xx = np.mgrid[0:H, 0:W]
    per_nucleus = []
    total_bright = total_faint = 0

    for nuc_id, (cy, cx) in enumerate(centers):
        # --- nucleus disk in DAPI channel (smooth, blurred edge) ---
        dist = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
        disk = DAPI_PEAK * np.clip(1.0 - (dist / NUC_RADIUS), 0.0, 1.0) ** 0.6
        # soften so it reads like a real blurred nucleus, not a hard cone
        disk = np.where(dist <= NUC_RADIUS + 2, disk, 0.0)
        dapi += disk

        # --- plant foci inside this nucleus ---
        # exact count: mean +/- small deterministic spread, clamped >=0
        count = int(np.clip(round(mean_foci + rng.normal(0, 1.0)), 0, None))
        # split into faint vs bright: ~1/3 of foci are faint (real but dim)
        n_faint = count // 3
        n_bright = count - n_faint

        placed = 0
        chosen: list[tuple[float, float]] = []
        attempts = 0
        while placed < count:
            attempts += 1
            # uniform within an inner radius so foci sit clearly inside the nucleus
            rr = NUC_RADIUS * 0.80 * np.sqrt(rng.random())
            th = rng.random() * 2 * np.pi
            fy = cy + rr * np.sin(th)
            fx = cx + rr * np.cos(th)
            # rejection-sample to keep planted foci individually resolvable
            ok = all((fy - py) ** 2 + (fx - px) ** 2 >= MIN_FOCUS_SEP ** 2
                     for py, px in chosen)
            if not ok and attempts < 10000:
                continue
            amp = FOCI_FAINT if placed < n_faint else FOCI_BRIGHT
            _add_blob(foci, fy, fx, amp, FOCUS_SIGMA)
            chosen.append((fy, fx))
            placed += 1

        total_bright += n_bright
        total_faint += n_faint
        per_nucleus.append({
            "nucleus_id": nuc_id,
            "center_yx": [int(cy), int(cx)],
            "foci": int(count),
            "faint": int(n_faint),
            "bright": int(n_bright),
        })

    # --- bright background speckles (noise that tempts over-counting) ---
    # placed anywhere (often outside nuclei); sharp + bright but NOT real foci.
    n_speckles = 14 if name == "ctrl" else 18
    speckle_pts = []
    for _ in range(n_speckles):
        sy = rng.integers(4, H - 4)
        sx = rng.integers(4, W - 4)
        _add_blob(foci, sy, sx, SPECKLE_AMP, SPECKLE_SIGMA)
        speckle_pts.append([int(sy), int(sx)])

    # --- noise everywhere ---
    dapi += rng.normal(0, DAPI_NOISE, size=dapi.shape)
    foci += rng.normal(0, FOCI_NOISE, size=foci.shape)
    # mild Poisson-like shot noise on the foci channel signal
    foci += rng.normal(0, np.sqrt(np.clip(foci, 0, None)) * 0.5)

    dapi_u16 = np.clip(dapi, 0, 65535).astype(np.uint16)
    foci_u16 = np.clip(foci, 0, 65535).astype(np.uint16)

    tifffile.imwrite(OUT_DIR / f"{name}_dapi.tif", dapi_u16)
    tifffile.imwrite(OUT_DIR / f"{name}_foci.tif", foci_u16)

    total_foci = sum(p["foci"] for p in per_nucleus)
    return {
        "condition": name,
        "n_nuclei": len(per_nucleus),
        "total_foci": int(total_foci),
        "mean_foci_per_nucleus": round(total_foci / len(per_nucleus), 3),
        "total_bright_foci": int(total_bright),
        "total_faint_foci": int(total_faint),
        "n_background_speckles": int(n_speckles),
        "speckle_centers_yx": speckle_pts,
        "per_nucleus": per_nucleus,
    }


def main() -> None:
    ctrl = _make_condition("ctrl", n_nuclei=12, mean_foci=2)
    treated = _make_condition("treated", n_nuclei=12, mean_foci=12)

    gt = {
        "seed": SEED,
        "image_shape": [H, W],
        "channels": {"dapi": "nuclear channel", "foci": "gamma-H2AX-like foci channel"},
        "focus_sigma_px": FOCUS_SIGMA,
        "notes": (
            "Foci are small Gaussian blobs inside nuclei in *_foci.tif; nuclei are "
            "blurred disks in *_dapi.tif. ~1/3 of foci per nucleus are FAINT (dim but "
            "real) and are missed unless detection sensitivity is increased. Bright, "
            "sharp background speckles are NOT foci and are over-counted by a too-loose "
            "detector; many sit outside nuclei and must be excluded by masking to nuclei."
        ),
        "ctrl": ctrl,
        "treated": treated,
    }
    (OUT_DIR / "ground_truth.json").write_text(json.dumps(gt, indent=2))

    # report
    tot = 0
    for p in OUT_DIR.glob("*.tif"):
        tot += p.stat().st_size
    print("wrote:")
    for p in sorted(OUT_DIR.iterdir()):
        print(f"  {p.name:22s} {p.stat().st_size:>8d} bytes")
    print(f"total tif bytes: {tot}")
    print(
        f"ctrl   : {ctrl['n_nuclei']} nuclei, {ctrl['total_foci']} foci, "
        f"mean {ctrl['mean_foci_per_nucleus']}/nucleus "
        f"({ctrl['total_faint_foci']} faint, {ctrl['n_background_speckles']} speckles)"
    )
    print(
        f"treated: {treated['n_nuclei']} nuclei, {treated['total_foci']} foci, "
        f"mean {treated['mean_foci_per_nucleus']}/nucleus "
        f"({treated['total_faint_foci']} faint, {treated['n_background_speckles']} speckles)"
    )


if __name__ == "__main__":
    main()
