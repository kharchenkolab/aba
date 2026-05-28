---
name: analyze-cell-migration-metrics
description: Quantify cell migration speed, directionality, and displacement from time-lapse microscopy image sequences
when_to_use: When tracking cells across frames from a wound-healing, chemotaxis, or motility assay
requires_tools: [run_python]
capabilities_needed: [trackpy, scikit-image, numpy, pandas, matplotlib]
keywords: [cell migration, motility, tracking, time-lapse, directionality, speed, displacement, wound healing]
produces: [raw_cell_detections.csv, all_trajectories.csv, filtered_trajectories.csv, cell_migration_metrics.csv, migration_summary.csv, cell_trajectories.png, rose_plot.png, track_displacement_plot.png]
domain: bioengineering
source: biomni:tool/bioengineering.py::analyze_cell_migration_metrics
---
# Analyze Cell Migration Metrics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load image sequence from a directory of TIF/PNG/JPG files or a multi-frame TIFF stack using `skimage.io.imread`.
2. Detect cells per frame with `trackpy.locate` (diameter ~15 px, minmass threshold); attach frame index.
3. Link detections into trajectories using `trackpy.link_df` (search_range=10, memory=3).
4. Filter short tracks with `trackpy.filter_stubs` (threshold = `min_track_length`).
5. For each surviving track compute per-cell metrics:
   - Convert pixel coordinates to µm using `pixel_size_um`.
   - Step distances → total path length; start-to-end vector → net displacement.
   - Directionality ratio = net_displacement / path_length.
   - Speed (µm/min) = path_length / (frames_tracked × time_interval_min).
6. Aggregate into a summary DataFrame; save per-cell CSV and summary CSV.
7. Plot all trajectories with `trackpy.plot_traj`; polar rose plot of migration angles; bar chart of displacements.

## Key decisions
- `trackpy.locate` parameters (diameter, minmass) may need tuning per dataset.
- `search_range` in linking balances ID-switches vs. missed links; default 10 px is conservative.
- `min_track_length` filters spurious detections; raise it for sparser, longer recordings.
- Acquisition frame rate is implicit in `time_interval_min`; no Hz assumption needed.

## Caveats
- Bright-field vs. fluorescence images behave differently with `tp.locate`; fluorescence recommended.
- Confluent monolayers or overlapping cells reduce tracking accuracy.
- Directionality ratio is undefined for stationary cells (path_length ≈ 0); guard with epsilon check.

## In ABA
Implement with `run_python`; `ensure_capability("trackpy", "scikit-image", "matplotlib")`. Original impl: `source` -> lift to lakeFS later.
