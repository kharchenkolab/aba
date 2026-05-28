---
name: analyze-crispr-genome-editing
description: Compare original and edited DNA sequences to characterize CRISPR-Cas9 editing outcomes — substitutions, indels, on-target efficiency, and HDR template incorporation.
when_to_use: When the user has a pre-/post-editing sequence pair and a guide RNA and wants to know what mutations occurred, whether they are on-target, and whether homology-directed repair (HDR) succeeded.
requires_tools: [run_python]
capabilities_needed: [biopython, pandas]
keywords: [CRISPR, Cas9, genome editing, indel, HDR, NHEJ, guide RNA, sgRNA, mutation analysis, pairwise alignment, genetics]
produces: [mutation list (substitutions + indels), on-target edit classification, HDR detection result, research log string]
domain: genetics
source: biomni:tool/genetics.py::analyze_crispr_genome_editing
---
# Analyze CRISPR Genome Editing

Distilled from a biomni implementation. In ABA, implement with the tools below
— not biomni.

## Approach
1. **Locate target site**: search for `guide_rna` in `original_sequence` with `str.find()`; if not found, try the reverse complement via `Bio.Seq.reverse_complement()`. Record position; warn if absent.
2. **Global pairwise alignment**: align `original_sequence` vs `edited_sequence` using `Bio.pairwise2.align.globalms(seq1, seq2, match=2, mismatch=-1, gap_open=-2, gap_extend=-0.5)`. Take the top-scoring alignment.
3. **Identify mutations** by walking the aligned strings character-by-character:
   - Gap in original (`orig=="-"`) → insertion in edited sequence.
   - Gap in edited (`edit=="-"`) → deletion.
   - Mismatch → substitution (`orig_base→edit_base`).
   - Track actual genomic position by counting non-gap characters in `aligned_orig[:i]`.
4. **On-target check**: determine whether any detected mutations fall within `range(target_site - 3, target_site + len(guide_rna) + 3)`.
5. **HDR detection** (if `repair_template` provided): extract a central marker substring of length `min(10, len(repair_template)//3)`; check presence in `edited_sequence` and absence in `original_sequence`. Positive → HDR likely; negative → NHEJ likely.
6. **Overall assessment**: report editing success, whether edits are on-target, and repair pathway.

## Key decisions
- Uses `Bio.pairwise2.align.globalms` (global alignment) with affine gap penalties — appropriate for sequences of similar length with localized edits.
- On-target window extends 3 bp upstream and downstream of the guide RNA to capture nearby PAM-site indels.
- HDR marker is a 10-bp slice from the center of the repair template — a heuristic that works when the template contains a unique inserted sequence.
- No PAM sequence validation is performed; assumes the caller has verified guide RNA design.

## Caveats
- `pairwise2` (Biopython) is deprecated in recent Biopython versions; prefer `Bio.Align.PairwiseAligner` for new code.
- On-target classification by string position matching is approximate; for production use CRISPResso2 which does proper amplicon-based analysis with UMI support.
- Single sequence pair only; for population-level allele frequency analysis use CRISPResso2 or CRISPECTOR.
- Does not handle complex rearrangements or large structural variants.

## In ABA
Implement with `run_python`; `ensure_capability(["biopython"])`. For amplicon deep-sequencing data prefer CRISPResso2 CLI (`crispresso2`). Original impl: `biomni:tool/genetics.py::analyze_crispr_genome_editing` → lift to lakeFS later.
