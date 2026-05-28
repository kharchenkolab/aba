---
name: calculate-similarity-metrics
description: Compute MSE, Pearson correlation, NCC, and mutual information between two medical images
when_to_use: When quantifying registration quality or comparing two aligned images numerically
requires_tools: [run_python]
capabilities_needed: [SimpleITK, numpy]
keywords: [similarity metrics, MSE, mutual information, correlation, NCC, SimpleITK, image comparison, registration QC]
produces: [dict with keys mutual_information, mean_squares, correlation, normalized_correlation]
domain: bioimaging
source: biomni:tool/bioimaging.py::calculate_similarity_metrics
---
# Calculate Similarity Metrics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load both images with `sitk.ReadImage`; convert to numpy with `sitk.GetArrayFromImage`.
2. Flatten both arrays and mask out non-finite values.
3. Compute metrics:
   - **MSE**: `np.mean((flat1 - flat2)**2)`; stored as negative (higher = better).
   - **Pearson correlation**: `np.corrcoef(flat1, flat2)[0, 1]`; returns 0 if either std is zero.
   - **NCC** (normalized cross-correlation): identical to Pearson in this implementation.
   - **Mutual information**: build a 50-bin 2D joint histogram with `np.histogram2d`, normalize to a joint probability `pxy`, then sum `pxy * log2(pxy / (px * py))`.
4. Return all four metrics in a dict; on any failure return zeros.

## Key decisions
- A small epsilon (1e-10) is added to histogram bins before MI calculation to avoid `log(0)`.
- MSE is negated so all metrics follow the "higher is better" convention.
- NCC and correlation are computed identically here (both use `np.corrcoef`).

## Caveats
- MI via histogram is an approximation; bin count (50) trades resolution for speed.
- Does not account for image masks or background regions; foreground-only metrics may be more informative.

## In ABA
Implement with `run_python`; `ensure_capability("SimpleITK")`, `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
