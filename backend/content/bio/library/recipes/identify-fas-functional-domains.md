---
name: identify-fas-functional-domains
description: Identify Fatty Acid Synthase (FAS) functional domains in a protein or nucleotide sequence by querying the HMMER web API against Pfam.
when_to_use: When a user has a FAS gene or protein sequence and wants to annotate its catalytic domains (KS, AT, KR, TE, DH, ER, ACP, PP-binding).
requires_tools: [run_python]
capabilities_needed: [biopython, requests]
keywords: [fatty acid synthase, FAS, domain annotation, Pfam, HMMER, polyketide, protein domains, lipid biosynthesis]
produces: [domain report TXT file, research log with FAS domain annotations]
domain: synthetic_biology
source: biomni:tool/synthetic_biology.py::identify_fas_functional_domains
---
# Identify FAS Functional Domains

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. If `sequence_type == "nucleotide"`, translate with `Bio.Seq.Seq.translate()` to obtain protein sequence.
2. POST protein sequence to `https://www.ebi.ac.uk/Tools/hmmer/search/hmmscan` with `hmmdb=pfam` (JSON body); extract the async `Location` URL from response headers.
3. Poll the result URL (`.json`) after a short delay; parse `results.hits` for domain hits with `ali_from`/`ali_to` positions and scores.
4. Match each hit name against a curated FAS domain dictionary (KS, KS-C, AT, KR, TE, DH, ER, ACP, PP-binding) by substring comparison.
5. Log all Pfam domains found and call out FAS-specific ones with position ranges and functional descriptions.
6. Write a full domain report to `output_file`; return a markdown research log.

## Key decisions
- HMMER/Pfam is the gold-standard method for domain annotation; no local installation required via the EBI REST API.
- The FAS domain dictionary maps Pfam accession substrings to human-readable names and biological functions, enabling structured output.
- Nucleotide input is translated first so a single code path handles both input types.

## Caveats
- Depends on network access to `ebi.ac.uk`; will fail in air-gapped environments. Consider a local `hmmscan` fallback.
- The 2-second sleep before polling may be insufficient for large sequences; implement retry logic in production.
- Only the first exon frame is translated; multi-exon or partial sequences may yield truncated proteins.

## In ABA
Implement with `run_python`; `ensure_capability("biopython")`, `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
