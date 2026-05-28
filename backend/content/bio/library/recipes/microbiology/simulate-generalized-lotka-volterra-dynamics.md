---
name: simulate-generalized-lotka-volterra-dynamics
description: Simulate multi-species microbial community dynamics using the generalized Lotka-Volterra (gLV) ODE model
when_to_use: When given species growth rates and an interaction matrix and asked to predict community composition over time
requires_tools: [run_python]
capabilities_needed: [scipy, numpy, pandas]
keywords: [Lotka-Volterra, gLV, microbial community, population dynamics, species interactions, microbiome, ecological modelling, ODE]
produces: [species abundance time-series CSV, dominant species, extinction count, research log]
domain: microbiology
source: biomni:tool/microbiology.py::simulate_generalized_lotka_volterra_dynamics
---
# Simulate Generalized Lotka-Volterra Dynamics

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate that `growth_rates` length and `interaction_matrix` shape both equal `len(initial_abundances)`.
2. Define gLV ODE: `dx_i/dt = x_i * (r_i + sum_j(A_ij * x_j))` where r is the growth rate vector and A is the interaction matrix.
3. Integrate with `scipy.integrate.odeint` over the provided time points.
4. Build a DataFrame with columns Time + Species_1 ... Species_N; save to CSV.
5. Compute summary statistics: initial and final total abundance, dominant species (argmax of final abundances), number of near-extinct species (abundance < 1e-6).
6. Return a structured log with model description, species count, time range, and summary statistics.

## Key decisions
- Interaction matrix sign convention: A[i,j] > 0 means species j promotes species i (mutualism/facilitation); A[i,j] < 0 means competition/inhibition.
- `odeint` (LSODA) is used; for stiff systems consider `solve_ivp` with `method="Radau"`.

## Caveats
- Deterministic model; does not capture demographic stochasticity relevant at low abundances.
- Negative abundances can arise numerically; clip to zero or use a solver with non-negativity constraints for long simulations.

## In ABA
Implement with `run_python`; `ensure_capability("scipy", "numpy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
