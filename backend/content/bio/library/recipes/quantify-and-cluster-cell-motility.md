---
name: quantify-and-cluster-cell-motility
description: Track cells across time-lapse microscopy frames, compute per-cell motility features (speed, directionality, MSD), and cluster them with k-means.
when_to_use: When given a directory of sequential time-lapse microscopy images and the goal is to identify distinct motility phenotypes within a cell population.
requires_tools: [run_python]
capabilities_needed: [opencv, numpy, pandas, scikit-learn]
keywords: [cell motility, cell tracking, time-lapse, k-means clustering, mean squared displacement, directionality, migration, live imaging]
produces: [cell_motility_features.csv, cluster_statistics.csv, research log]
domain: cell_biology
source: biomni:tool/cell_biology.py::quantify_and_cluster_cell_motility
---
# Quantify and Cluster Cell Motility

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load all `.tif/.tiff/.png/.jpg/.jpeg` files from the input directory in sorted order.
2. On the first frame: Otsu threshold → find contours (`cv2.findContours`) → filter area > 50 px → compute centroids via moments; store as initial cell list.
3. For each subsequent frame: detect centroids the same way; nearest-neighbour match each tracked cell to the closest new centroid within 50 px; remove matched centroids from the candidate pool.
4. Discard cells tracked for fewer than 3 frames.
5. Per tracked cell compute:
   - `avg_speed`: mean inter-frame displacement (px/frame)
   - `directionality`: net displacement / total path length
   - `msd`: mean distance from start position across all frames
   - `track_duration` and `track_length`
6. Z-score normalise the three motility features; run `KMeans(n_clusters=num_clusters, random_state=42)`.
7. Save per-cell feature table and per-cluster summary statistics (mean ± std).

## Key decisions
- Nearest-neighbour tracking with a hard 50 px gap threshold; cells that disappear for a frame are permanently lost.
- Only speed, directionality, and MSD are used for clustering — duration/length are saved but excluded from the feature matrix.
- `num_clusters` defaults to 3; caller should vary and inspect elbow/silhouette to choose.

## Caveats
- Simple nearest-neighbour tracker fails in dense or fast-moving populations; consider TrackPy for higher accuracy.
- Images must be greyscale-compatible; colour images are read via `IMREAD_GRAYSCALE`.
- Pixel units are used throughout; supply a px-to-µm conversion factor externally if physical units are needed.

## In ABA
Implement with `run_python`; `ensure_capability("opencv-python", "pandas", "scikit-learn", "numpy")`. Original impl: `source` -> lift to lakeFS later.
