---
name: get-plasmid-sequence
description: Retrieve a plasmid sequence from Addgene (by numeric ID) or NCBI (by name)
when_to_use: Use when given an Addgene plasmid ID or a plasmid name and needing its full sequence
requires_tools: [run_python]
capabilities_needed: [biopython, requests, beautifulsoup4]
keywords: [plasmid, Addgene, NCBI, vector, sequence, repository, cloning]
produces: [plasmid_sequence, source_metadata]
domain: molecular_biology
source: biomni:tool/molecular_biology.py::get_plasmid_sequence
---
# Get Plasmid Sequence

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Auto-detect source: if `identifier.isdigit()` or `is_addgene=True`, use Addgene; otherwise use NCBI.
2. **Addgene path**: GET `https://www.addgene.org/<id>/sequences/`; parse HTML with BeautifulSoup; find `<textarea class="copy-from">`; strip FASTA header `> Addgene NGS Result`; join remaining lines removing spaces.
3. **NCBI path**: `Entrez.esearch(db="nuccore", term="Cloning vector <name>", retmax=1, sort="relevance")`; take first ID; `Entrez.efetch(db="nuccore", id=..., rettype="fasta", retmode="text")`; parse with `SeqIO.read(..., "fasta")`; return `str(sequence.seq)`.
4. Return dict with `source`, `identifier`, and `sequence`.

## Key decisions
- Numeric identifiers are assumed to be Addgene IDs; named identifiers go to NCBI.
- `is_addgene` parameter can override auto-detection.
- Addgene scrapes the NGS-verified sequence from the public page (no API key required).

## Caveats
- Addgene HTML structure may change; scraping is fragile.
- NCBI search query prefixes with "Cloning vector" which biases results; may miss some plasmids.
- No authentication; Addgene may gate some sequences behind login.
- Set `Entrez.email` before calling the NCBI path.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`, `ensure_capability("requests")`, `ensure_capability("beautifulsoup4")`. Original impl: `source` -> lift to lakeFS later.
