---
name: perform-crispr-cas9-genome-editing
description: Simulate CRISPR-Cas9 guide RNA validation, target site identification, delivery efficiency, and indel outcome prediction
when_to_use: When planning or reporting a CRISPR-Cas9 experiment — validating gRNAs, estimating efficiency, and predicting editing outcomes for a given cell type
requires_tools: [run_python]
capabilities_needed: [numpy]
keywords: [CRISPR, Cas9, guide RNA, gRNA, genome editing, indel, PAM, GC content, delivery efficiency]
produces: [crispr_results/original_sequence.txt, crispr_results/modified_sequence.txt]
domain: bioengineering
source: biomni:tool/bioengineering.py::perform_crispr_cas9_genome_editing
---
# Perform CRISPR-Cas9 Genome Editing (Simulation)

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Validate each guide RNA: must be exactly 20 nt, ATGC only. Compute GC content; flag as optimal (40–60 %) or suboptimal.
2. Search the target genomic locus for each valid guide. Check for a downstream NGG PAM (positions guide+1..guide+3). Score guides: +1 for optimal GC, +2 for confirmed PAM.
3. Assign delivery efficiency from a cell-type lookup table (HEK293: 0.85, HeLa: 0.75, iPSC: 0.60, primary neuron: 0.40, HSC: 0.55, mouse embryo: 0.70, plant cell: 0.30; default 0.50 for unknown types). Report recommended delivery method (lipofection).
4. Select the highest-scoring guide. Compute cut site (3 bp upstream of PAM). Predict editing efficiency = delivery_efficiency × (0.5 + score × 0.1).
5. Simulate a deletion indel (random 1–5 bp) at the cut site; produce modified sequence.
6. Write original and modified sequences as FASTA files to `crispr_results/`.
7. Return a structured research log covering all five steps plus summary.

## Key decisions
- PAM check is simplified (position-based string match); real workflows should use a full off-target scorer (e.g., CRISPRscan, Rule Set 2).
- Indel size is stochastic; repeat runs will differ; use a fixed seed for reproducibility.
- Delivery efficiency table is a rough heuristic — update from experimental data when available.

## Caveats
- This is a planning/simulation tool; actual editing rates depend on chromatin accessibility, cell health, and transfection quality.
- Off-target analysis is not included; use Cas-OFFinder or CRISPOR for production workflows.
- Guide sequences must be uppercase ATGC; RNA (AUGC) inputs will fail validation.

## In ABA
Implement with `run_python`; `ensure_capability("numpy")`. Original impl: `source` -> lift to lakeFS later.
