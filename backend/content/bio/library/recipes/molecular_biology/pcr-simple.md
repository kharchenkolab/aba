---
name: pcr-simple
description: Simulate PCR amplification by finding all products from forward and reverse primer binding sites
when_to_use: Use when predicting PCR products from a template sequence and two primers, including circular templates
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [PCR, primer, amplification, amplicon, product size, binding site, circular template]
produces: [pcr_products, product_sequences, binding_sites]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::pcr_simple
---
# PCR Simple

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Align forward primer to template allowing up to 1 mismatch (both strands).
2. Align `reverse_complement(reverse_primer)` to template allowing up to 1 mismatch; this finds the reverse primer binding positions on the forward strand.
3. If either primer has no alignments, return failure with binding site counts.
4. For each `(fwd_pos, rev_pos)` pair: if `fwd_pos < rev_pos`, extract linear product `seq[fwd_pos : rev_pos + len(rev_primer)]`; if circular and `fwd_pos >= rev_pos`, wrap product as `seq[fwd_pos:] + seq[:rev_pos + len(rev_primer)]`.
5. Record product size, sequence, primer positions, and any mismatches.
6. Return all products sorted by size with binding site counts.

## Key decisions
- Reverse primer is reverse-complemented before alignment so positions on the forward strand are found directly.
- Circular template wrap-around is handled explicitly; linear templates skip inverted primer pairs.
- Up to 1 mismatch per primer is tolerated (mirrors real-world PCR conditions).

## Caveats
- No Tm or extension time modeling; purely positional.
- Multiple products can occur if primers bind at multiple locations.
- Does not model primer dimers or hairpin structures.
- Product sizes assume primer sequences are fully included in the product.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")` for `Bio.Seq.reverse_complement`. Original impl: `source` -> lift to lakeFS later.
