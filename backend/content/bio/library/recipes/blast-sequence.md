---
name: blast-sequence
description: Submit a DNA or protein sequence to NCBI BLAST via Biopython and return the top alignment hit
when_to_use: When identifying an unknown sequence, verifying sequence identity, or finding homologs against NCBI databases
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [BLAST, sequence alignment, NCBI, blastn, blastp, homology, e-value, identity, coverage, nucleotide, protein]
produces: [hit ID, hit definition, accession, e-value, percent identity, percent coverage]
domain: database
source: biomni:tool/database.py::blast_sequence
---
# BLAST Sequence

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Wrap the input string in `Bio.Seq.Seq`.
2. Submit via `Bio.Blast.NCBIWWW.qblast(program, database, sequence, expect=100, word_size=7, megablast=True)`.
3. Parse the result handle with `Bio.Blast.NCBIXML.parse`; take the first record.
4. From the first alignment's first HSP, extract: `hit_id`, `hit_def`, `accession`, `e_value` (`hsp.expect`), `identity` (`hsp.identities / hsp.align_length * 100`), `coverage` (`len(hsp.query) / len(sequence) * 100`).
5. Enforce a wall-clock timeout of 600 s; return an error string on timeout or no hits.

## Key decisions
- For DNA sequences: `database="core_nt"`, `program="blastn"`.
- For protein sequences: `database="nr"`, `program="blastp"`.
- `megablast=True` speeds up nucleotide searches; disable for distantly related sequences.
- Only the very first alignment/HSP is returned (best hit by e-value ordering from NCBI).

## Caveats
- `NCBIWWW.qblast` is a synchronous blocking call that can take minutes for long sequences or busy NCBI servers.
- NCBI rate-limits remote BLAST; do not submit many jobs in parallel without delay.
- Short or low-complexity sequences may return no alignments.

## In ABA
Implement with `run_python` and `biopython` (`Bio.Blast.NCBIWWW`, `Bio.Blast.NCBIXML`, `Bio.Seq`); `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
