---
name: retrieve-topk-repurposing-drugs-from-disease-txgnn
description: Query pre-computed TxGNN drug-repurposing predictions to return top-K candidate drugs for a disease
when_to_use: Drug repurposing hypothesis generation from a disease name using a knowledge-graph GNN model
requires_tools: [run_python]
capabilities_needed: [numpy, pickle]
keywords: [drug repurposing, TxGNN, knowledge graph, disease, drug candidates, GNN]
produces: [ranked list of drug names with sigmoid-transformed prediction scores]
domain: pharmacology
source: biomni:tool/pharmacology.py::retrieve_topk_repurposing_drugs_from_disease_txgnn
---
# Retrieve Top-K Repurposing Drugs via TxGNN

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load two pickle files from `data_lake_path`: `txgnn_name_mapping.pkl` (id→name maps) and `txgnn_prediction.pkl` (disease→{drug_id: score}).
2. Fuzzy-match the input `disease_name` against available disease keys using `difflib.get_close_matches` (cutoff 0.6).
3. Retrieve raw prediction scores for the matched disease.
4. Apply sigmoid transformation: `1 / (1 + exp(-score))` to convert raw logits to probabilities.
5. Sort descending, take top-K, map drug IDs to human-readable names via `id2name_drug`.
6. Return a formatted summary listing ranked drugs with their scores.

## Key decisions
- Fuzzy matching with cutoff 0.6 tolerates minor disease name variations; returns an error if no match found.
- Sigmoid is applied post-hoc to raw TxGNN logit scores; the data lake stores raw values.

## Caveats
- Depends on pre-computed TxGNN predictions stored in the data lake; not a live model call.
- Disease coverage limited to diseases present in the TxGNN training graph.

## In ABA
Implement with `run_python`; `ensure_capability("numpy")`. Requires `txgnn_name_mapping.pkl` and `txgnn_prediction.pkl` in the data lake. Original impl: `source` -> lift to lakeFS later.
