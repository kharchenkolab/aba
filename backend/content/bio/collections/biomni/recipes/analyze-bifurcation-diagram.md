---
name: analyze-bifurcation-diagram
description: Construct a bifurcation diagram from parameter-swept time-series data and classify dynamical regimes (stable, periodic, chaotic).
when_to_use: When a user has simulated a dynamical system across a parameter sweep and wants to visualise bifurcations and identify regime transitions.
requires_tools: [run_python]
capabilities_needed: [numpy, scipy, matplotlib]
keywords: [bifurcation, dynamical systems, chaos, Lyapunov exponent, periodicity, regime classification, nonlinear dynamics]
produces: [PNG bifurcation diagram, research log with regime transitions]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::analyze_bifurcation_diagram
---
# Analyze Bifurcation Diagram

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that `len(parameter_values) == time_series_data.shape[0]`.
2. For each time series, discard the first 30% as transient; analyse the steady-state tail.
3. Find local maxima with `scipy.signal.find_peaks`; count them as a periodicity indicator.
4. Estimate the largest Lyapunov exponent as `log(mean(|diffs|))` of consecutive differences; assign -1 for near-zero variance (stable).
5. Collect attractor sampling points: use peak values where peaks exist, else last 5 points.
6. Classify each parameter value: positive Lyapunov → chaotic; 0 peaks → stable; 1/2/4 peaks → period-1/2/4; >4 peaks → higher period or chaotic.
7. Plot attractor points as tiny black dots against parameter value; shade background by regime colour (blue=stable, green=period-1, yellow=period-2, pink=period-4, salmon=chaotic).
8. Detect regime boundaries from consecutive changes in `regimes` array; annotate transitions in log.
9. Save 300 dpi PNG and return the research log.

## Key decisions
- Transient discard (first 70%) ensures attractor representation, not transient dynamics.
- Lyapunov proxy is a heuristic (not full Jacobian-based); sufficient for classification, not for precise exponent values.
- Regime colouring makes bifurcation structure visually obvious without needing curve-fitting.

## Caveats
- The Lyapunov estimate is approximate; dense chaotic windows may be misclassified near period-doubling cascades.
- `list.index` on `parameter_values` for boundary lookup is O(n); fine for typical sweep sizes (<10 000).

## In ABA
Implement with `run_python`; `ensure_capability("scipy")`, `ensure_capability("matplotlib")`. Original impl: `source` -> lift to lakeFS later.
