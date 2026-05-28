---
name: query-arxiv
description: Search arXiv for papers by keyword query and return titles and abstracts
when_to_use: When a user wants to find preprints or papers on arXiv by topic, author, or keyword
requires_tools: [run_python]
capabilities_needed: [arxiv]
keywords: [arxiv, preprint, search, literature, abstract, paper discovery]
produces: [list of paper titles and summaries]
domain: literature
source: biomni:tool/literature.py::query_arxiv
---
# Query arXiv

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Import `arxiv` library.
2. Create an `arxiv.Client()` instance.
3. Build an `arxiv.Search(query=query, max_results=max_papers, sort_by=arxiv.SortCriterion.Relevance)`.
4. Iterate `client.results(search)` and format each paper as `Title: ...\nSummary: ...`.
5. Join results with double newlines; return "No papers found on arXiv." if empty.

## Key decisions
- Sort by `Relevance` (not recency) to surface the most pertinent results first.
- Return both title and full summary (abstract) for each paper.

## Caveats
- The `arxiv` Python client queries the arXiv API; rate limits apply for bulk requests.
- Summaries can be long; consider truncating for display in chat contexts.

## In ABA
Implement with `run_python`; `ensure_capability("arxiv")`. Original impl: `source` -> lift to lakeFS later.
