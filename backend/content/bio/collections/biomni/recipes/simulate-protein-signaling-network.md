---
name: simulate-protein-signaling-network
description: Simulate protein signaling network dynamics using ODE-based logic modeling with normalized Hill functions
when_to_use: Given network topology (activators/inhibitors), Hill function parameters, and species initial values, simulate time-course concentrations of all signaling proteins
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [signaling network, Hill function, ODE, logic modeling, phosphorylation, protein dynamics, activation, inhibition, kinase]
produces: [protein_signaling_simulation_results.csv, final concentration summary]
domain: systems_biology
source: biomni:tool/systems_biology.py::simulate_protein_signaling_network
---
# Simulate Protein Signaling Network

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Collect all unique protein names from `network_structure` (targets and regulators); sort for stable indexing.
2. For each regulator-target pair, compute a normalized Hill term: activation = `W * x^n / (x^n + EC50^n)`; inhibition = `W * (1 - x^n / (x^n + EC50^n))`.
3. Average all regulation terms for a target; clamp to [0, 1]; compute `dy/dt = (1/tau) * (regulation * ymax - y)`.
4. Initialize concentrations from `species_params["y0"]`; solve with `solve_ivp` (LSODA, rtol=1e-6, atol=1e-9).
5. Write time-series for all proteins to `protein_signaling_simulation_results.csv`; report final concentrations.

## Key decisions
- Regulation terms averaged (not summed) to keep the combined signal in [0, 1]; this is a normalized logic modeling convention.
- LSODA chosen for robustness across both stiff and non-stiff regimes.
- Proteins not listed in `network_structure` as targets are treated as constant inputs (their dy/dt = 0).

## Caveats
- The averaging scheme for multiple regulators is a simplification; AND-gate or OR-gate logic requires explicit formulations.
- No explicit degradation term beyond the ymax-driven homeostasis in the Hill ODE; basal turnover must be encoded in tau and ymax.
- Model is qualitative; absolute concentrations depend on the units chosen for ymax and tau.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
