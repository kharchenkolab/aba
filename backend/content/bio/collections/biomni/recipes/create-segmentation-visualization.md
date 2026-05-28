---
name: create-segmentation-visualization
description: Render multi-view (axial, sagittal, coronal, combined) PNG overlays of a segmentation mask on an MRI
when_to_use: After nnUNet (or any) segmentation to produce human-readable QC figures
requires_tools: [run_python]
capabilities_needed: [nilearn, matplotlib]
keywords: [segmentation, visualization, NIfTI, overlay, axial, sagittal, coronal, nilearn, MRI, QC]
produces: ["segmentation_overlay.png", "segmentation_axial.png", "segmentation_sagittal.png", "segmentation_coronal.png"]
domain: bioimaging
source: biomni:tool/bioimaging.py::create_segmentation_visualization
---
# Create Segmentation Visualization

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that both `original_mri` and `segmentation` NIfTI paths exist.
2. Use `nilearn.plotting.plot_roi` with `bg_img=original_mri`, `cmap="Set1"`, `alpha=0.6` for each view.
3. Save four figures to `output_dir`:
   - `segmentation_overlay.png` — default tri-planar view.
   - `segmentation_axial.png` — `display_mode="z"`.
   - `segmentation_sagittal.png` — `display_mode="x"`.
   - `segmentation_coronal.png` — `display_mode="y"`.
4. Call `display.close()` after each save to release memory.
5. Return the list of saved file paths.

## Key decisions
- `matplotlib` backend set to `Agg` (non-interactive) to avoid display dependency.
- `dpi=150` with `bbox_inches="tight"` for compact but readable output.
- ImportError for nilearn returns an empty list rather than raising.

## Caveats
- nilearn renders at native MRI resolution; very large volumes can be slow.
- Label colormap (`Set1`) works best for up to 8 distinct classes.

## In ABA
Implement with `run_python`; `ensure_capability("nilearn")`, `ensure_capability("matplotlib")`. Original impl: `source` -> lift to lakeFS later.
