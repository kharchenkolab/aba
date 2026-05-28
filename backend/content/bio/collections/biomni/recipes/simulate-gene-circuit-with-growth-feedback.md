---
name: simulate-gene-circuit-with-growth-feedback
description: Numerically integrate a gene regulatory circuit ODE system that couples gene expression dynamics to cell growth via a metabolic burden term.
when_to_use: When a user wants to simulate a synthetic gene circuit with explicit growth-expression coupling, such as studying metabolic burden or circuit-growth tradeoffs.
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [gene circuit, ODE simulation, growth feedback, Hill function, metabolic burden, synthetic biology, gene regulation, LSODA]
produces: [NPZ time-series data file, JSON parameter file, research log]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::simulate_gene_circuit_with_growth_feedback
---
# Simulate Gene Circuit with Growth Feedback

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Parse `circuit_topology` (n_genes × n_genes adjacency matrix), `kinetic_params`, and `growth_params`.
2. Define ODE system with state vector `[gene_1, ..., gene_n, cell_mass]`:
   - For gene i: production starts at `basal_rates[i]`; each non-zero topology entry j applies a Hill-function regulatory factor. Positive entries multiply by `(1 + w * Hill)` (activation); negative entries by `(1 + w * (1 - Hill))` (repression).
   - Dilution combines intrinsic degradation with growth-dilution `(d[cell_mass]/dt) / cell_mass`.
   - Cell mass: `d[mass]/dt = max_growth_rate * mass / (1 + growth_inhibition * sum(gene_weights * expressions))`.
3. Set initial state: all genes at 0.1, cell_mass at 1.0.
4. Integrate with `scipy.integrate.solve_ivp` (LSODA, rtol=1e-6, atol=1e-9) over `[0, simulation_time]`.
5. Save time-series arrays to `.npz` and parameters to `.json` in `gene_circuit_results/`.
6. Return a text research log with final expression levels, cell mass, and effective growth rate.

## Key decisions
- LSODA is chosen for automatic stiffness detection, important when growth and expression timescales differ by orders of magnitude.
- Dilution by growth rate is approximated using the instantaneous derivative; a fully self-consistent formulation requires coupling the cell-mass ODE more carefully.
- Hill function form: `x^n / (K^n + x^n)` gives saturable, switch-like regulation.

## Caveats
- The dilution term uses `dxdt[n_genes]` before it is fully resolved in the same step; this is an approximation that may introduce small errors at high growth rates.
- No numerical check that `cell_mass > 0` throughout; could diverge for extreme parameters.
- Results are saved relative to the working directory; set an appropriate `cwd` when calling.

## In ABA
Implement with `run_python`; `ensure_capability("scipy")`, `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
