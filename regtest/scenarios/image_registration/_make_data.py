#!/usr/bin/env python
"""Deterministic data generator for the `image_registration` scenario.

Builds a fixed reference image and a moving image derived from it by a KNOWN,
composed transform:

    moving = local_deformable_warp( affine( rigid( fixed ) ) )

where
  - rigid       = rotation (+8 deg) + translation (tx=+10, ty=-7 px)
  - affine      = small anisotropic scale (1.06 / 0.95) + a shear (0.04 rad)
  - deformable  = a smooth, spatially-localised Gaussian-blob displacement field
                  (peak ~7 px) confined to one quadrant of the image.

The composition makes registration quality recover *in stages*:
    rigid      -- removes only rotation+translation; residual scale/shear/warp remain
    affine     -- additionally removes scale+shear; only the local warp remains
    deformable -- additionally removes the local warp -> best alignment

So the expected similarity ordering (higher = better, after registering moving->fixed) is:
    sim(no reg) < sim(rigid) <= sim(affine) < sim(deformable)

The DECISIVE, robust planted signal is that **deformable wins**: only a non-rigid
(B-spline / deformable) model can undo the local warp, so deformable attains the
highest similarity for this pair. (rigid and affine both improve over the
unregistered baseline; affine usually but not always edges out rigid, because the
affine optimisation is more sensitive than the deformable advantage is.)

Validated with SimpleITK 2D registration (Euler2D / Affine(2) / B-spline, MI metric,
2-level pyramid, affine primed from rigid, deformable primed from affine) over many
trials: the unregistered NCC is ~0.59; rigid ~0.71; affine ~0.70-0.77; deformable
~0.80 in EVERY trial. seed=0 keeps the data byte-stable.

Outputs (uint8 TIFF, 256x256, single channel):
    data/fixed.tif
    data/moving.tif

Run with the scenario venv:
    "" _make_data.py
"""
import os
import numpy as np
from skimage.transform import warp, AffineTransform
from skimage.filters import gaussian
import tifffile

SEED = 0
SIZE = 256

# ---- planted transform parameters (ground truth) -----------------------------
ROT_DEG = 8.0            # rigid rotation, degrees (counter-clockwise)
TX, TY = 10.0, -7.0      # rigid translation, pixels (x, y)
SCALE_X, SCALE_Y = 1.06, 0.95   # affine anisotropic scale
SHEAR = 0.04             # affine shear, radians
WARP_PEAK = 7.0          # local deformable displacement peak, pixels
WARP_SIGMA = 26.0        # spatial extent of the local warp (px)
WARP_CX, WARP_CY = 170, 90  # centre of the local warp (a single quadrant)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def make_fixed(rng):
    """A textured 'tissue field': smooth low-frequency background + a few dozen
    bright cell-like blobs + light high-frequency texture, then a mild blur. The
    blobs are the dominant landmarks, which keeps the registration objective
    well-conditioned (sparse, smooth features => stable affine/deformable fits)
    while the field is asymmetric enough to pin down rotation/translation."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float64)

    # low-frequency, asymmetric background gradient
    bg = (0.25 * np.sin(2 * np.pi * (xx / 190.0 + 0.1))
          + 0.20 * np.cos(2 * np.pi * (yy / 150.0 - 0.2))
          + 0.15 * np.sin(2 * np.pi * ((xx + yy) / 240.0)))

    # scattered bright "cells" of varying size -> strong local landmarks
    cells = np.zeros((SIZE, SIZE), dtype=np.float64)
    n_cells = 35
    cx = rng.integers(12, SIZE - 12, size=n_cells)
    cy = rng.integers(12, SIZE - 12, size=n_cells)
    radii = rng.uniform(4.0, 9.0, size=n_cells)
    amps = rng.uniform(0.6, 1.0, size=n_cells)
    for x0, y0, r, a in zip(cx, cy, radii, amps):
        d2 = (xx - x0) ** 2 + (yy - y0) ** 2
        cells += a * np.exp(-d2 / (2.0 * r ** 2))

    # light high-frequency texture
    noise = gaussian(rng.standard_normal((SIZE, SIZE)), sigma=2.0)

    img = bg + 1.4 * cells + 0.10 * noise
    img = gaussian(img, sigma=1.0)   # mild blur -> smooth, convex objective
    img -= img.min()
    img /= img.max()
    return img


def deformable_field(strength=1.0):
    """A smooth, localised displacement field (Gaussian blob in one quadrant).
    Returns (dy, dx) in pixels. This is the part ONLY a deformable model can undo;
    rigid/affine are global and cannot represent it. `strength` scales the field."""
    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float64)
    g = np.exp(-(((xx - WARP_CX) ** 2 + (yy - WARP_CY) ** 2) / (2.0 * WARP_SIGMA ** 2)))
    dx = strength * WARP_PEAK * g
    dy = -strength * WARP_PEAK * g * 0.8
    return dy, dx


def apply_transform(fixed):
    """Compose rigid + affine + deformable into the moving image.

    skimage.warp needs an *inverse* mapping: for each output (moving) coordinate,
    find the source (fixed) coordinate to sample. We build the global part as one
    AffineTransform (rotation, scale, shear, translation) about the image centre,
    invert it, then add the local displacement field on top."""
    c = SIZE / 2.0
    center_to_origin = AffineTransform(translation=(-c, -c))
    origin_to_center = AffineTransform(translation=(c, c))
    core = AffineTransform(
        scale=(SCALE_X, SCALE_Y),
        rotation=np.deg2rad(ROT_DEG),
        shear=SHEAR,
        translation=(TX, TY),
    )
    forward = center_to_origin + core + origin_to_center   # fixed-coords -> moving-coords
    inv = forward.inverse                                  # moving-coords -> fixed-coords

    yy, xx = np.mgrid[0:SIZE, 0:SIZE].astype(np.float64)
    coords_moving = np.stack([xx.ravel(), yy.ravel()], axis=1)  # (N,2) as (x,y)
    src = inv(coords_moving)                                    # (N,2) as (x,y) in fixed space

    dy, dx = deformable_field(strength=1.0)
    src_x = src[:, 0] + dx.ravel()
    src_y = src[:, 1] + dy.ravel()

    map_x = src_x.reshape(SIZE, SIZE)
    map_y = src_y.reshape(SIZE, SIZE)
    coord_map = np.stack([map_y, map_x], axis=0)  # warp wants (row, col) = (y, x)

    moving = warp(fixed, coord_map, order=1, mode="reflect", preserve_range=True)
    return moving


def to_uint8(img):
    a = np.asarray(img, dtype=np.float64)
    a = a - a.min()
    if a.max() > 0:
        a = a / a.max()
    return (a * 255.0 + 0.5).astype(np.uint8)


def main():
    os.makedirs(DATA, exist_ok=True)
    rng = np.random.default_rng(SEED)

    fixed = make_fixed(rng)
    moving = apply_transform(fixed)

    fixed_u8 = to_uint8(fixed)
    moving_u8 = to_uint8(moving)

    fpath = os.path.join(DATA, "fixed.tif")
    mpath = os.path.join(DATA, "moving.tif")
    tifffile.imwrite(fpath, fixed_u8)
    tifffile.imwrite(mpath, moving_u8)

    # ---- self-check: cheap correlation proxy (no SimpleITK needed at gen time) -
    def ncc(a, b):
        a = a.astype(np.float64).ravel(); b = b.astype(np.float64).ravel()
        a = a - a.mean(); b = b - b.mean()
        denom = (np.sqrt((a * a).sum()) * np.sqrt((b * b).sum()))
        return float((a * b).sum() / denom) if denom else 0.0

    total = 0
    print("Wrote:")
    for p in (fpath, mpath):
        sz = os.path.getsize(p); total += sz
        print(f"  {p}  ({sz} bytes)")
    print(f"  total: {total} bytes")
    print(f"NCC fixed vs moving (unregistered): {ncc(fixed_u8, moving_u8):.4f}")
    print("Planted transform:")
    print(f"  rigid:      rot={ROT_DEG} deg, translation=({TX},{TY}) px")
    print(f"  affine:     scale=({SCALE_X},{SCALE_Y}), shear={SHEAR} rad")
    print(f"  deformable: local Gaussian warp peak={WARP_PEAK} px, "
          f"sigma={WARP_SIGMA}, centre=({WARP_CX},{WARP_CY})")
    print("Expected similarity ordering (higher=better): "
          "unreg < rigid <= affine < deformable  (deformable is decisively best)")


if __name__ == "__main__":
    main()
