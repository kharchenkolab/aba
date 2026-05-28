---
name: query-pubmed
description: Search PubMed for biomedical papers and return titles, abstracts, and journals
when_to_use: When a user needs to find biomedical or life-science literature from PubMed/MEDLINE
requires_tools: [run_python]
capabilities_needed: [pymed]
keywords: [pubmed, medline, biomedical literature, abstract, journal, NCBI, paper search]
produces: [list of paper titles, abstracts, and journal names]
domain: literature
source: biomni:tool/literature.py::query_pubmed
---
# Query PubMed

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Import `PubMed` from `pymed`.
2. Instantiate `PubMed(tool="MyTool", email="<valid-email>")` — NCBI requires a valid contact email.
3. Call `pubmed.query(query, max_results=max_papers)` and collect into a list.
4. If no results, retry up to `max_retries` times: on each retry strip the last word(s) from the query and wait 1 second between attempts.
5. Format each paper as `Title: ...\nAbstract: ...\nJournal: ...` and join with double newlines.

## Key decisions
- Progressive query simplification (drop trailing words) handles overly-specific queries that return zero hits.
- Add a 1-second sleep between retries to respect NCBI rate limits.

## Caveats
- Use a real, project-specific email in the `PubMed()` constructor; NCBI may block requests with placeholder addresses.
- `pymed` wraps the Entrez E-utilities API; the API is free but rate-limited (3 req/s without API key, 10/s with).
- Abstract or journal fields may be `None` for some records.

## In ABA
Implement with `run_python`; `ensure_capability("pymed")`. Original impl: `source` -> lift to lakeFS later.
