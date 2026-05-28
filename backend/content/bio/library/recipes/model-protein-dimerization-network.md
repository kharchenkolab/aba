---
name: model-protein-dimerization-network
description: Solve ODE-based protein dimerization networks to find equilibrium monomer and dimer concentrations
when_to_use: Given monomer concentrations, pairwise association constants (Ka), and network topology, compute equilibrium species concentrations
requires_tools: [run_python]
capabilities_needed: [numpy, scipy]
keywords: [protein dimerization, equilibrium, association constant, Ka, ODE, mass action, protein-protein interaction, network]
produces: [equilibrium monomer concentrations, equilibrium dimer concentrations, simulation log]
domain: systems_biology
source: biomni:tool/systems_biology.py::model_protein_dimerization_network
---
# Model Protein Dimerization Network

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Parse `network_topology` (list of (monomer1, monomer2) tuples) and look up each pair's Ka in `dimerization_affinities`; try both `A-B` and `B-A` key orderings.
2. Build initial state vector: monomers first (indexed), then dimers (all zero).
3. Define ODE system with mass-action kinetics: formation rate = Ka * [A] * [B], dissociation rate = 1.0 * [dimer] (kon = Ka, koff = 1 by convention).
4. Integrate with `scipy.integrate.solve_ivp` using BDF method (stiff), t_span=(0, 1000), rtol=1e-6, atol=1e-9.
5. Read final time-point values as equilibrium concentrations; report monomers and dimers.

## Key decisions
- koff fixed at 1.0 so that the equilibrium ratio [dimer]/([A][B]) = Ka as required; absolute time units are arbitrary.
- BDF integrator chosen for potentially stiff systems with large Ka differences.
- Dimer name resolution tries forward then reverse direction to handle caller ordering.

## Caveats
- Model assumes all dimers are independent (no cooperative effects between different dimer species).
- Equilibrium correctness depends on t_span being long enough; highly stable dimers (large Ka) may need t_span > 1000.
- Higher-order complexes (trimers, oligomers) are not modeled.

## In ABA
Implement with `run_python`; `ensure_capability("numpy", "scipy")`. Original impl: `source` -> lift to lakeFS later.
