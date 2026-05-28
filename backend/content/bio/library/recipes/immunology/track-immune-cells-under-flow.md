---
name: track-immune-cells-under-flow
description: Segment, track, and classify immune cell behaviors (rolling, arrest, crawling, diapedesis) from intravital or in-vitro flow microscopy sequences
when_to_use: When given a time-lapse image sequence or video of immune cells under shear flow and needing quantified behavioral categories and trajectory statistics
requires_tools: [run_python]
capabilities_needed: [opencv-python, numpy, pandas, trackpy]
keywords: [cell tracking, intravital microscopy, rolling, arrest, crawling, diapedesis, leukocyte, flow chamber, trackpy]
produces: [cell_trajectories.csv with behavior labels, summary statistics log]
domain: immunology
source: biomni:tool/immunology.py::track_immune_cells_under_flow
---
# Track Immune Cells Under Flow

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image sequence from directory (PNG/JPG/TIF) or video file using `cv2`.
2. For each frame: equalize histogram, Gaussian blur (5x5), adaptive threshold (Gaussian, inverted), morphological opening, `cv2.connectedComponentsWithStats` to detect cells in area range 20–500 px².
3. Extract per-detection features: centroid x/y, area, width, height, roundness = min(w,h)/max(w,h).
4. Link detections across frames with `trackpy.link` (search_range=20 px, memory=3 frames); filter short tracks with `trackpy.filter_stubs` (min 5 frames).
5. For each track compute frame-to-frame displacement, speed (px/s converted to µm/s), and flow-alignment cosine based on `flow_direction`.
6. Classify behaviors per time point:
   - **Rolling**: flow_alignment > 0.7 and speed < threshold
   - **Arrest**: speed < 20% of threshold for ≥ 5 consecutive frames
   - **Crawling**: slow motion (< 50% threshold) following an arrest period
   - **Diapedesis**: crawling cells with roundness < 0.5
7. Concatenate all tracks, save `cell_trajectories.csv`, report average speed and track length.

## Key decisions
- `speed_threshold = 5.0 * pixel_size_µm / time_interval_sec` is the base threshold; scale with imaging parameters.
- Roundness < 0.5 as diapedesis proxy (cell elongation during transmigration).
- Graceful fallback to synthetic tracks if frame loading or trackpy key errors occur.

## Caveats
- Adaptive thresholding performs poorly on low-contrast or uneven illumination; preprocessing (flat-field correction) may be needed.
- Flow direction must be set correctly; misalignment causes wrong rolling/arrest classification.
- Trackpy `link` is single-processor; large datasets may be slow.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "numpy", "pandas", "trackpy")`. Original impl: `source` -> lift to lakeFS later.
