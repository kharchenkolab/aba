---
name: segment-cells-with-deep-learning
description: Segment individual microbial cells in fluorescence images using Cellpose/Omnipose pre-trained deep learning models
when_to_use: When asked to segment cells with a neural network model, especially rod-shaped bacteria or irregular morphologies that defeat classical watershed
requires_tools: [run_python]
capabilities_needed: [cellpose, scikit-image, matplotlib, numpy]
keywords: [cell segmentation, Cellpose, Omnipose, deep learning, fluorescence microscopy, bacteria, instance segmentation, bact_fluor_omni]
produces: [cell masks TIFF, outlines overlay PNG, cell count, area statistics, research log]
domain: microbiology
source: biomni:tool/microbiology.py::segment_cells_with_deep_learning
---
# Segment Cells with Deep Learning (Cellpose/Omnipose)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image with `skimage.io.imread`; extract first channel if multi-channel.
2. Instantiate `cellpose.models.CellposeModel(model_type=model_type, gpu=False)`. Default model is `bact_fluor_omni` (Omnipose bacterial fluorescence).
3. Run `model.eval(img, diameter=diameter, channels=[0,0], flow_threshold=0.4, do_3D=False)`. Accept 3- or 4-value returns (masks, flows, [styles], diams).
4. Auto-estimate diameter if not provided (reported in log).
5. Count cells: `len(np.unique(masks)) - 1` (subtract background).
6. Save masks as uint16 TIFF; save contour overlay with `plt.contour` on original image as PNG.
7. Compute per-cell areas from mask labels; report mean ± std.
8. Return a research log covering model config, cell count, area statistics, and output paths.

## Key decisions
- `channels=[0,0]` means single-channel (no nuclear channel); adjust to `[2,1]` for cytoplasm+nucleus.
- Handle both 3- and 4-return-value API variants for Cellpose version compatibility.
- GPU disabled by default for portability; set `gpu=True` for large images.

## Caveats
- Requires cellpose ≥ 2.x; Omnipose models may need the omnipose package.
- Auto-diameter estimation can fail on very sparse or very dense images; provide explicit diameter when known.

## In ABA
Implement with `run_python`; `ensure_capability("cellpose", "scikit-image", "matplotlib", "numpy")`. Original impl: `source` -> lift to lakeFS later.
