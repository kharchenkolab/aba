---
name: annotate-bacterial-genome
description: Annotate a bacterial genome FASTA using Prokka to identify genes, CDS, rRNA, tRNA, and other features
when_to_use: When given an assembled bacterial genome in FASTA format and asked to predict genes or generate GFF/GenBank annotation
requires_tools: [run_python]
capabilities_needed: [prokka]
keywords: [genome annotation, Prokka, bacterial genome, GFF, GenBank, genes, CDS, rRNA, tRNA, functional annotation]
produces: [GFF3 file, GenBank file, protein FASTA, nucleotide gene FASTA, annotation summary TXT, research log]
domain: microbiology
source: biomni:tool/microbiology.py::annotate_bacterial_genome
---
# Annotate Bacterial Genome

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Verify the genome FASTA file exists; create the output directory.
2. Build a Prokka CLI command: `prokka <genome.fasta> --outdir <dir> --prefix <prefix>` with optional `--genus`, `--species`, `--strain` flags when provided.
3. Run Prokka via `subprocess.run(..., check=True, capture_output=True)`.
4. Parse stdout for lines containing "Found" to extract per-feature-type counts.
5. List all output files matching the prefix and report their sizes.
6. Return a structured log: input path, runtime, feature counts, output file listing, and explanation of key file formats (.gff, .gbk, .faa, .ffn, .txt).

## Key decisions
- Auto-generate a timestamp-based prefix if none is supplied.
- Propagate Prokka stderr on failure for diagnostics.
- Feature count parsing relies on Prokka's "Found X: N" log format.

## Caveats
- Prokka must be installed and on PATH (`conda install -c bioconda prokka` or equivalent).
- Genus/species hints improve protein database matching but are optional.
- Very fragmented assemblies or non-standard organisms may yield lower annotation completeness.

## In ABA
Implement with `run_python` (subprocess call to prokka); `ensure_capability("prokka")`. Original impl: `source` -> lift to lakeFS later.
