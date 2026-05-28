---
name: perform-flux-balance-analysis
description: Load a genome-scale metabolic model (SBML or JSON), apply reaction constraints, set an objective, solve the FBA linear program with COBRApy, and report the flux distribution.
when_to_use: Given a COBRA-compatible metabolic model file, predict steady-state metabolic fluxes (e.g., growth rate, byproduct secretion) under specified nutrient or genetic constraints.
requires_tools: [run_python]
capabilities_needed: [cobra, pandas]
keywords: [flux balance analysis, FBA, metabolic model, COBRA, cobrapy, genome-scale model, SBML, steady-state fluxes, systems biology, metabolic network]
produces: [flux distribution CSV (reaction_id, reaction_name, flux, bounds)]
domain: systems_biology
source: biomni:tool/systems_biology.py::perform_flux_balance_analysis
---
# Perform Flux Balance Analysis

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. **Load the model** using COBRApy based on file extension:
   - `.xml` / `.sbml` → `cobra.io.read_sbml_model(model_file)`
   - `.json` → `cobra.io.load_json_model(model_file)`
   - other → `cobra.io.load_model(model_file)` (BiGG shorthand names)
   Log reaction and metabolite counts.

2. **Apply constraints** — for each entry in the `constraints` dict (`reaction_id → (lb, ub)`), retrieve the reaction by ID and set `reaction.bounds = (lb, ub)`. Silently skip unknown reaction IDs with an error note.

3. **Set objective** — if `objective_reaction` is provided, assign `model.objective = objective_reaction`; otherwise retain the model's default objective (typically the biomass reaction).

4. **Solve** — call `model.optimize()`; check `solution.status` (should be `"optimal"`) and record `solution.objective_value`.

5. **Build flux table** — construct a pandas DataFrame with columns `reaction_id`, `reaction_name`, `flux`, `lower_bound`, `upper_bound` for all reactions; save to `output_file` (default `fba_results.csv`).

6. **Report** — count active reactions (`|flux| > 1e-6`); list the top 10 reactions by absolute flux magnitude.

## Key decisions
- No pre-processing of the model (no media normalization, no gene knockouts) beyond user-supplied bounds.
- Active-reaction threshold is 1e-6 (numerical zero for LP solvers).
- Top-10 ranking uses `abs(flux).argsort()[::-1]` on the full DataFrame (not just active reactions).
- Default solver is whatever COBRApy selects (typically GLPK or CPLEX if licensed).

## Caveats
- FBA assumes pseudo-steady-state; does not capture dynamic or regulatory responses.
- The solution may be non-unique (alternative optima exist); use FVA (`cobra.flux_analysis.variability`) to characterize the solution space.
- No loopless constraint applied; thermodynamically infeasible cycles may carry flux.
- Model must be mass-balanced and have appropriate exchange reaction bounds to reflect the growth medium.

## In ABA
Implement with `run_python`; `ensure_capability(cobra, pandas)`. For richer analyses, extend with flux variability analysis (`cobra.flux_analysis.flux_variability_analysis`) or gene essentiality screens (`single_gene_deletion`). Original impl: `source` -> lift to lakeFS later.
