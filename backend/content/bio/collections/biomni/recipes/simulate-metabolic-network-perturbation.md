---
name: simulate-metabolic-network-perturbation
description: Load a COBRA SBML metabolic model, simulate kinetic dynamics with mass-action ODEs, apply a metabolite perturbation, and report concentration and flux time-courses
when_to_use: Given an SBML model file, initial metabolite concentrations, and a perturbation spec, simulate the dynamic response of the metabolic network
requires_tools: [run_python]
capabilities_needed: [cobra, numpy, scipy, pandas]
keywords: [metabolic network, COBRA, SBML, kinetic simulation, mass action, perturbation, flux, ODE, metabolite concentration]
produces: [metabolite_concentrations.csv, reaction_fluxes.csv, perturbation response summary]
domain: systems_biology
source: biomni:tool/systems_biology.py::simulate_metabolic_network_perturbation
---
# Simulate Metabolic Network Perturbation

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load SBML model with `cobra.io.read_sbml_model`; extract metabolite IDs and reaction stoichiometry.
2. Initialize concentration array from `initial_concentrations`; default to 1.0 for unspecified metabolites.
3. Implement simple mass-action kinetics: reaction rate = product of substrate concentrations raised to stoichiometric coefficients.
4. Define ODE: dC/dt accumulated from stoichiometric coefficients × rates over all reactions.
5. At `perturbation_params["time"]`, multiply the target metabolite concentration by `perturbation_params["factor"]` (applied once with a flag).
6. Solve with `scipy.integrate.solve_ivp` (LSODA); save concentration and flux DataFrames to CSV.
7. Identify metabolites with >5% relative concentration change immediately after perturbation; report top 5.

## Key decisions
- LSODA method handles mixed stiff/non-stiff dynamics automatically.
- Mass-action kinetics are a simplification; real metabolic networks use enzyme-kinetic rate laws (Michaelis-Menten, allosteric).
- Perturbation flag prevents repeated application across solver sub-steps at the same time point.

## Caveats
- Does not perform FBA; this is a kinetic ODE simulation seeded from a stoichiometric model structure.
- Mass-action assumption is biologically unrealistic for most enzymatic reactions; results are qualitative.
- Large models may be slow; consider reducing network scope or using a dedicated kinetic modeling tool (COPASI, tellurium).

## In ABA
Implement with `run_python`; `ensure_capability("cobra", "numpy", "scipy", "pandas")`. Original impl: `source` -> lift to lakeFS later.
