---
name: analyze-tissue-deformation-flow
description: Compute optical-flow-based tissue deformation metrics (divergence, curl, strain) from a time-lapse microscopy image sequence using Lucas-Kanade tracking on a regular grid.
when_to_use: When given a sequence of microscopy images of tissue and the goal is to quantify local expansion/contraction, rotation, and mechanical strain across frames.
requires_tools: [run_python]
capabilities_needed: [opencv, numpy]
keywords: [tissue deformation, optical flow, Lucas-Kanade, divergence, curl, strain tensor, time-lapse, biomechanics, morphogenesis, biophysics]
produces: ["flow_viz_NNN.png per frame pair", "divergence/curl/strain NNN.npy per frame pair", "deformation_summary.npy with mean/max statistics", research log]
domain: biophysics
source: biomni:tool/biophysics.py::analyze_tissue_deformation_flow
---
# Analyze Tissue Deformation Flow

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load images: if the sequence is a list of paths, read each with `cv2.imread(IMREAD_GRAYSCALE)`; if a numpy array convert colour frames to greyscale.
2. Create a regular grid of feature points every 20 px (`np.mgrid`).
3. For each consecutive frame pair, run `cv2.calcOpticalFlowPyrLK` (winSize 15×15, maxLevel 2); keep only points with `status==1`.
4. Build a sparse flow field: store displacement vectors at valid grid positions; zero elsewhere.
5. Compute per-pixel derivatives using `cv2.Sobel` (ksize=3, scaled by `1/(8·pixel_scale)`):
   - Divergence = ∂u/∂x + ∂v/∂y (expansion/contraction)
   - Curl = ∂v/∂x − ∂u/∂y (rotation)
   - Strain tensor: ε_xx=∂u/∂x, ε_yy=∂v/∂y, ε_xy=½(∂u/∂y+∂v/∂x); magnitude = Frobenius norm.
6. Save per-pair: flow visualisation PNG (green arrows on greyscale background), divergence/curl/strain as `.npy`.
7. Summarise: mean divergence, max divergence, mean |curl|, mean strain across all frame pairs.

## Key decisions
- Lucas-Kanade is chosen for sparse tracking on a grid; dense Farneback flow (`cv2.calcOpticalFlowFarneback`) would give smoother fields.
- `pixel_scale` converts pixel units to physical units (µm/pixel) for properly scaled derivatives.
- Nearest-neighbour assignment of sparse vectors into the flow field keeps implementation simple but leaves gaps; production use should interpolate (e.g., `scipy.interpolate.griddata`).

## Caveats
- Large deformations between frames can cause tracking failure; reduce inter-frame interval or increase pyramid levels.
- The sparse flow field has zeros at untracked locations, which biases divergence/curl calculations; mask zeros before computing derivatives in high-accuracy applications.
- Results are saved as `.npy` arrays; downstream visualisation requires additional plotting code.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy")`. Original impl: `source` -> lift to lakeFS later.
