---
name: annotate-plasmid
description: Annotate a DNA sequence with known plasmid features using pLannotate
when_to_use: Use when given a plasmid or vector sequence and needing to identify promoters, resistance genes, origins of replication, and other standard genetic elements
requires_tools: [run_python]
capabilities_needed: [plannotate, pandas]
keywords: [plasmid, vector, annotation, promoter, resistance, ori, pLannotate, snapgene, feature]
produces: [annotation_table, feature_list, coordinates]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::annotate_plasmid
---
# Annotate Plasmid

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Write the input sequence to a temporary FASTA file (`>sequence\n<seq>`).
2. Run `plannotate batch -i <fasta> -o <tmpdir> -f output --csv -d` via subprocess; add `-l` flag if `is_circular=False`.
3. Read the output CSV at `<tmpdir>/output_pLann.csv` using `pandas.read_csv`.
4. Return the records as a list of dicts with fields: `sseqid`, `qstart`, `qend`, `sframe`, `score`, `evalue`, `Feature`, `Description`, `Type`, `pident`, `percmatch`, `fragment`, etc.

## Key decisions
- `-d` (detailed) flag enables comprehensive search across the pLannotate database.
- Topology is passed as absence of `-l` (linear) flag; circular is the default.
- Results include both high-confidence hits and partial/fragmented matches (`fragment` field).

## Caveats
- Requires `plannotate` CLI installed and accessible in PATH.
- Uses subprocess; wrap in error handling since subprocess failure returns None.
- For very large sequences, plannotate may be slow; consider chunking or pre-filtering.

## In ABA
Implement with `run_python`; `ensure_capability("plannotate")`, `ensure_capability("pandas")`. Original impl: `source` -> lift to lakeFS later.
