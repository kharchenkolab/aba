---
name: design-knockout-sgrna
description: Retrieve top-ranked SpCas9 knockout sgRNAs for a gene from a pre-computed library
when_to_use: When designing CRISPR knockout experiments and need optimized guide RNA sequences for a human or mouse gene
requires_tools: [run_python]
capabilities_needed: [pandas]
keywords: [CRISPR, sgRNA, guide RNA, knockout, KO, Cas9, gene editing, human, mouse]
produces: [sgRNA sequences ranked by combined score]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::design_knockout_sgrna
---
# Design Knockout sgRNA

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Resolve the library path for the requested species (human → `sgRNA_KO_SP_human.txt`, mouse → `sgRNA_KO_SP_mouse.txt`); both are TSV files with columns `Target Gene Symbol`, `Combined Rank`, `sgRNA Sequence`.
2. Load with `pd.read_csv(..., delimiter="\t")`.
3. Upper-case the gene symbol; filter rows where `Target Gene Symbol` matches exactly (case-insensitive). Fall back to `str.contains` if no exact match.
4. Sort ascending by `Combined Rank`, take top `num_guides` rows.
5. Return dict: `gene_name`, `species`, `guides` (list of sequences), `explanation`.

## Key decisions
- Combined Rank is the primary sorting key (lower = better); no secondary scoring needed.
- Partial-match fallback prevents silent empty returns when the user supplies a gene alias.
- Return empty `guides` list (not an error) when the gene is absent from the library.

## Caveats
- Library files must be accessible from the data-lake path; raise `FileNotFoundError` with a clear message if missing.
- Only human and mouse libraries are included; other species will raise `KeyError`.
- Library reflects a fixed SpCas9 / NGG design; other Cas variants are not covered.

## In ABA
Implement with `run_python`; `ensure_capability("pandas")`. Resolve the library path from the project data-lake. Original impl: `source` -> lift to lakeFS later.
