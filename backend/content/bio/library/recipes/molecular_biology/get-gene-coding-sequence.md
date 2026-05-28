---
name: get-gene-coding-sequence
description: Retrieve the coding sequence(s) of a gene from NCBI Entrez by gene name and organism
when_to_use: Use when needing the CDS of a specific gene from a named organism; returns spliced coding sequences from RefSeq
requires_tools: [run_python]
capabilities_needed: [biopython]
keywords: [CDS, coding sequence, NCBI, Entrez, gene, RefSeq, mRNA, nucleotide]
produces: [cds_sequences, refseq_ids]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::get_gene_coding_sequence
---
# Get Gene Coding Sequence

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Set `Bio.Entrez.email` if provided; required for NCBI API compliance.
2. Search NCBI Gene database: `Entrez.esearch(db="gene", term="<organism>[Organism] AND <gene>[Gene]", retmax=5)` and take the first ID.
3. Fetch gene record in XML: `Entrez.efetch(db="gene", id=gene_id, rettype="gb", retmode="xml")`; extract RefSeq accession from `Entrezgene_locus[0]["Gene-commentary_accession"]` + version.
4. Fetch full RefSeq record: `Entrez.efetch(db="nucleotide", id=refseq_id, rettype="gbwithparts", retmode="text")`, parse with `SeqIO.read(..., "genbank")`.
5. Iterate features; for each `feature.type == "CDS"` whose `gene` qualifier matches `gene_name`, extract sequence with `feature.location.extract(seq_record).seq`.
6. Return list of `{refseq_id, sequence}` dicts.

## Key decisions
- Uses `gbwithparts` retrieval to get complete sequence including all CDS features.
- Matches CDS features by exact gene name from qualifiers to avoid off-target genes.
- Returns all CDS isoforms found, not just the first.

## Caveats
- Requires internet access to NCBI; rate-limited without API key.
- Gene name matching is case-sensitive against qualifier values.
- NCBI XML structure can vary; KeyError/IndexError is caught and re-raised.
- Set `Entrez.email` to a valid address to avoid NCBI throttling.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`. Original impl: `source` -> lift to lakeFS later.
