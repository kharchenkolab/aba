---
name: search-google
description: Run a Google search and return titles, URLs, and descriptions for top results
when_to_use: When a user needs general web search results (protocols, news, product pages, non-indexed databases)
requires_tools: [run_python]
capabilities_needed: [googlesearch-python]
keywords: [google search, web search, URL, protocol, discovery]
produces: [formatted string of result titles, URLs, and descriptions]
domain: literature
source: biomni:tool/literature.py::search_google
---
# Search Google

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Import `search` from the `googlesearch` package (`googlesearch-python`).
2. Call `search(query, num_results=num_results, lang=language, advanced=True)` which yields result objects.
3. For each result extract `res.title`, `res.url`, `res.description`.
4. Concatenate into a formatted string: `Title: ...\nURL: ...\nDescription: ...\n\n`.
5. Return the full string (or empty string on error).

## Key decisions
- `advanced=True` enables the richer result objects with title/url/description fields.
- Default `num_results=3` keeps requests lightweight; increase for broader sweeps.
- Language defaults to `"en"`; pass ISO code to localise results.

## Caveats
- The `googlesearch-python` library scrapes Google and is subject to rate limiting / CAPTCHA blocks.
- No API key required but repeated calls from the same IP may get throttled; add delays for bulk usage.
- Results are snippets only; pair with `extract-url-content` to get full page text.

## In ABA
Implement with `run_python`; `ensure_capability("googlesearch-python")`. Original impl: `source` -> lift to lakeFS later.
