---
name: query-scholar
description: Search Google Scholar for the top matching paper for a given query
when_to_use: When a user needs to find a peer-reviewed paper or citation metadata from Google Scholar
requires_tools: [run_python]
capabilities_needed: [scholarly]
keywords: [google scholar, citation, peer review, paper search, bibliography, literature]
produces: [title, year, venue, abstract of top result]
domain: literature
source: biomni:tool/literature.py::query_scholar
---
# Query Google Scholar

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Import `scholarly` and `ProxyGenerator` from the `scholarly` package.
2. Set up free proxies via `ProxyGenerator().FreeProxies()` and `scholarly.use_proxy(pg)` to reduce rate-limit blocks.
3. Call `scholarly.search_pubs(query)` to get a generator.
4. Retrieve the first result with `next(search_query, None)`.
5. Return formatted string: `Title`, `Year`, `Venue`, `Abstract` from `result['bib']`.

## Key decisions
- Only the first result is returned; scholarly's free-proxy path is fragile for bulk queries.
- Using `ProxyGenerator().FreeProxies()` is best-effort; expect occasional failures.

## Caveats
- Google Scholar has aggressive rate-limiting; the proxy approach is unreliable in production.
- Consider caching results or using a paid SerpAPI key for more stable access.
- `pub_year` and `venue` fields may be absent for some records.

## In ABA
Implement with `run_python`; `ensure_capability("scholarly")`. Original impl: `source` -> lift to lakeFS later.
