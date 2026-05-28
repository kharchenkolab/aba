---
name: engineer-bacterial-genome-for-therapeutic-delivery
description: Insert promoters, genes, terminators, and therapeutic cargo into a bacterial genome FASTA, then render a circular feature map.
when_to_use: When a user wants to in-silico engineer a bacterial chassis for therapeutic payload delivery by inserting defined genetic parts at specified positions.
requires_tools: [run_python]
capabilities_needed: [biopython, reportlab]
keywords: [synthetic biology, bacterial engineering, genome editing, therapeutic delivery, genetic parts, plasmid map, promoter, terminator]
produces: [engineered genome FASTA, circular PDF plasmid map, research log]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::engineer_bacterial_genome_for_therapeutic_delivery
---
# Engineer Bacterial Genome for Therapeutic Delivery

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Load the reference genome from a FASTA file using `Bio.SeqIO.read`.
2. Convert the sequence to a mutable string; initialise a `position_adjustment` counter.
3. Insert **promoters** first: splice each sequence at `part["position"] + position_adjustment`, increment the counter, propagate position offsets to downstream parts (genes, terminators), and record a `SeqFeature(type="promoter")`.
4. Repeat for **genes** (propagate offsets to terminators) and **terminators**.
5. Insert **therapeutic cargo** after the last recorded feature end position; record a `SeqFeature(type="therapeutic_cargo")`.
6. Wrap the final string in a `SeqRecord`, write as FASTA.
7. Build a `Bio.Graphics.GenomeDiagram` circular diagram; colour promoters green, genes blue, terminators red, cargo purple; save as PDF.
8. Return a markdown research log summarising each step and output filenames.

## Key decisions
- Position adjustment is cumulative: each insertion shifts all downstream coordinates so parts land at their intended loci.
- The cargo position defaults to just after the last inserted feature when no explicit position is given.
- `GenomeDiagram` circular format at 1000×1000 pt produces a publication-ready map.

## Caveats
- Large genomes (>10 Mb) will be slow to manipulate as plain strings; consider indexed approaches for real chromosomes.
- No conflict detection: overlapping insertions silently overwrite. Validate positions before running.
- `GenomeDiagram` requires `reportlab`; ensure it is installed.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`, `ensure_capability("reportlab")`. Original impl: `source` -> lift to lakeFS later.
