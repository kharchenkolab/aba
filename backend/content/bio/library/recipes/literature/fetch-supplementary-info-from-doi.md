---
name: fetch-supplementary-info-from-doi
description: Fetch and download supplementary materials for a paper given its DOI
when_to_use: When a user needs supplementary files (tables, datasets, appendices) linked from a publisher page identified by DOI
requires_tools: [run_python]
capabilities_needed: [requests, beautifulsoup4]
keywords: [supplementary, DOI, publisher, appendix, supplemental materials, paper download]
produces: [downloaded supplementary files, research log]
domain: literature
source: biomni:tool/literature.py::fetch_supplementary_info_from_doi
---
# Fetch Supplementary Info from DOI

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Resolve the DOI to a publisher URL via `https://doi.org/<doi>` using `requests.get` (follows redirects automatically).
2. Fetch the publisher landing page HTML.
3. Parse with `BeautifulSoup(response.content, "html.parser")`.
4. Iterate all `<a href=...>` tags; keep those whose link text contains "supplementary", "supplemental", or "appendix".
5. Resolve relative hrefs to absolute URLs with `urllib.parse.urljoin(publisher_url, href)`.
6. For each candidate URL, `requests.get` and write bytes to `output_dir/<filename>`.
7. Return a log string and list of downloaded file paths.

## Key decisions
- Use `response.url` (after redirect) as the base URL for `urljoin`, not the original `doi.org` URL.
- Keyword match on link text (lowercased), not URL path, to catch varied publisher conventions.
- Set `User-Agent: Mozilla/5.0` header to avoid bot blocks.

## Caveats
- Publisher pages vary wildly; this heuristic misses materials behind JS-rendered links or paywalled direct downloads.
- Some publishers require cookies/sessions; success rate is best for open-access journals.
- The filename is taken from the last URL segment — may collide if multiple files share a name.

## In ABA
Implement with `run_python`; `ensure_capability("requests", "beautifulsoup4")`. Original impl: `source` -> lift to lakeFS later.
