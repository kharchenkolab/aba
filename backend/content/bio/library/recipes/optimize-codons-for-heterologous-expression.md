---
name: optimize-codons-for-heterologous-expression
description: Replace each codon in a target gene with the highest-frequency synonymous codon from a host organism's codon usage table.
when_to_use: When a user wants to improve heterologous expression by adapting a DNA or RNA sequence to the codon preferences of a specific host organism.
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [codon optimization, heterologous expression, codon usage, synonymous codons, synthetic biology, gene design, host adaptation]
produces: [optimized sequence file, original sequence file, research log with statistics]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::optimize_codons_for_heterologous_expression
---
# Optimize Codons for Heterologous Expression

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Detect sequence type: RNA if `"U"` present; normalise to DNA by replacing U→T.
2. Warn if sequence length is not divisible by 3.
3. Split into triplet codons; build codon→amino acid map from `Bio.Data.CodonTable.standard_dna_table` (add stop codons as `"*"`).
4. Build amino acid→[(codon, frequency)] map using `host_codon_usage` dict (default 0 for missing codons); sort each list descending by frequency.
5. For each original codon: look up amino acid, pick the top-frequency synonymous codon from the host table; keep original on `KeyError`.
6. Join optimised codons; convert back to RNA if input was RNA.
7. Calculate % codons changed; write original and optimised sequences to `original_sequence.txt` / `optimized_sequence.txt`.
8. Return markdown log with counts and filenames.

## Key decisions
- "Most frequent codon" strategy (deterministic maximum-frequency selection) is the simplest effective approach; probabilistic sampling can be added for more natural codon distributions.
- Non-standard codons are silently preserved with a warning rather than raising an error.
- DNA normalisation before processing allows RNA inputs without a separate code path.

## Caveats
- Does not account for rare-codon pausing (beneficial for protein folding) or mRNA secondary structure effects.
- Host codon usage table must cover most codons; zero-frequency entries cause the original codon to be retained.
- Stop codons in the input are preserved; passing a CDS without a stop codon is fine.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
