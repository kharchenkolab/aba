---
name: analyze-protein-colocalization
description: Quantify colocalization between two fluorescent channels using Pearson and Manders coefficients
when_to_use: When a user has two-channel fluorescence microscopy images and needs colocalization statistics
requires_tools: [run_python]
capabilities_needed: [scikit-image, scipy, matplotlib, numpy]
keywords: [colocalization, fluorescence, Pearson, Manders, microscopy, protein interaction, overlap coefficient]
produces: [Pearson correlation whole and masked, Manders overlap coefficient MOC, M1 M2 coefficients, overlay PNG, research log]
domain: physiology
source: biomni:tool/physiology.py::analyze_protein_colocalization
---
# Analyze Protein Colocalization

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load both channel images with `skimage.io.imread`; convert RGB to grayscale by channel mean if needed.
2. Normalize each image to [0,1] with `skimage.exposure.rescale_intensity`.
3. Threshold each channel (otsu / li / yen via `skimage.filters`) to produce binary foreground masks.
4. Compute Pearson's r on the full image and on the union mask using `scipy.stats.pearsonr`.
5. Compute Manders Overlap Coefficient: sum(ch1 * ch2) / sqrt(sum(ch1²) * sum(ch2²)).
6. Compute M1 = sum(ch1 * mask2) / sum(ch1) and M2 = sum(ch2 * mask1) / sum(ch2).
7. Generate a 2×2 figure: channel 1 (green), channel 2 (red), RGB overlay, hexbin scatter plot; save PNG.

## Key decisions
- Three threshold methods supported; Otsu is the default and works well for most fluorescence images.
- Pearson above threshold (combined mask) is often more meaningful than the whole-image value because background pixels dilute the signal.
- MOC is symmetric; M1 and M2 are directional and answer "how much of protein A is where protein B is".

## Caveats
- Images must be the same pixel dimensions; co-registration is the user's responsibility.
- Pearson can be inflated by bright-field bleed-through; confirm channel separation before interpreting.
- No pixel-by-pixel randomization test (Costes) is included; add if statistical significance is needed.

## In ABA
Implement with `run_python`; `ensure_capability(["scikit-image", "scipy", "matplotlib", "numpy"])`. Original impl: `source` -> lift to lakeFS later.
