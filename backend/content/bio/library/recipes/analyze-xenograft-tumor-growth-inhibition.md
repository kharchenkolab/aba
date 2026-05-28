---
name: analyze-xenograft-tumor-growth-inhibition
description: Analyze tumor growth inhibition (TGI) in xenograft models with statistics and growth-curve plots
when_to_use: When comparing tumor volume trajectories across treatment groups in an in vivo xenograft experiment
requires_tools: [run_python]
capabilities_needed: [pandas, scipy, statsmodels, matplotlib, numpy]
keywords: [xenograft, TGI, tumor growth, in vivo, PDX, treatment efficacy, ANOVA, Tukey]
produces: [TGI percentages per group, repeated-measures ANOVA table, Tukey HSD results, tumor growth curve PNG, summary CSV]
domain: pharmacology
source: biomni:tool/pharmacology.py::analyze_xenograft_tumor_growth_inhibition
---
# Xenograft Tumor Growth Inhibition Analysis

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load a CSV/TSV file with columns for time, tumor volume, treatment group, and subject ID.
2. Compute per-group mean ± SEM at each time point and save a statistics CSV.
3. Fit per-subject linear regression to derive individual growth rates; report mean ± SEM per group.
4. Calculate TGI (%) at the final time point relative to the first group (assumed control): `TGI = (ctrl_mean - trt_mean) / ctrl_mean * 100`.
5. Run repeated-measures ANOVA (`statsmodels.formula.api.ols` + `sm.stats.anova_lm(type=2)`) with group × time interaction and subject as a blocking term.
6. Post-hoc pairwise comparisons at the final time point using Tukey's HSD (`statsmodels.stats.multicomp.pairwise_tukeyhsd`).
7. Plot mean ± SEM growth curves with `matplotlib.pyplot.errorbar`; save PNG at 300 dpi.

## Key decisions
- First group in the data is treated as the vehicle/control for TGI calculation.
- Growth rate estimated by simple linear regression (slope = mm³/day); exponential models are not applied.
- ANOVA formula includes subject as a fixed effect to approximate a repeated-measures structure.

## Caveats
- True mixed-effects repeated-measures models (e.g., via `pingouin` or `statsmodels MixedLM`) are more rigorous; the linear ANOVA is a simplification.
- TGI is computed only at the last observed time point; area-under-the-curve TGI is not calculated.
- Assumes balanced time points across subjects; missing data may skew statistics.

## In ABA
Implement with `run_python`; `ensure_capability("matplotlib", "statsmodels", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
