---
name: simulate-demographic-history
description: Simulate DNA sequences under specified demographic and coalescent models using msprime and export segregating-site statistics and VCF output
when_to_use: When asked to generate synthetic genomic sequence data under population-genetic demographic scenarios (bottleneck, expansion, contraction, sawtooth) for downstream analysis or benchmarking
requires_tools: [run_python]
capabilities_needed: [msprime]
keywords: [coalescent, demographic history, msprime, simulation, bottleneck, population expansion, VCF, nucleotide diversity, ARG]
produces: [vcf_file, simulation_stats_log]
domain: genetics
source: biomni:tool/genetics.py::simulate_demographic_history
---
# Simulate Demographic History

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Parse `demographic_model` string and `demographic_params` dict to build an `msprime.Demography` object:
   - `constant`: single `add_population` at size N.
   - `bottleneck`: initial size → shrink at `T_recovery` → recover at `T_bottleneck` (note: msprime times go backwards, so recovery is the more recent event).
   - `expansion`: population at `N_initial` until `T_expansion`, then `N_final`.
   - `contraction`: population at `N_initial` until `T_contraction`, then `N_final`.
   - `sawtooth`: a list of `N_values` (len = len(times)+1) applied at each change time.
2. Select coalescent model: `msprime.StandardCoalescent()` (Kingman) or `msprime.BetaCoalescent(alpha=beta_coalescent_param)`.
3. Run `msprime.sim_ancestry(samples, recombination_rate, sequence_length, demography, model, random_seed)`.
4. Overlay mutations with `msprime.sim_mutations(ts, rate=mutation_rate, random_seed=random_seed)`.
5. Write VCF with `mts.write_vcf(vcf_file)`.
6. Report nucleotide diversity (`mts.diversity()`), number of segregating sites, and number of trees in the ARG.

## Key decisions
- For sawtooth, `N_values` must have exactly one more element than `times`; raise `ValueError` otherwise.
- A sensible default (`N=10000` constant) is used when `demographic_params` is None.
- `random_seed` enables reproducibility across runs.

## Caveats
- Diploid vs haploid distinction matters: `msprime.sim_ancestry` samples diploid individuals by default; set `ploidy=1` for haploid.
- The beta-coalescent is appropriate for organisms with high variance in offspring number (e.g. marine species); default alpha=1.5 if not specified.
- Output VCF may be large for long sequences with high mutation rates; consider chunking.

## In ABA
Implement with `run_python`; `ensure_capability("msprime")`. Original impl: `source` -> lift to lakeFS later.
