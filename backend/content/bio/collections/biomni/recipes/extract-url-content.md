---
name: extract-url-content
description: Extract clean readable text from a webpage URL
when_to_use: When a user has a URL and needs the readable textual content of that page (not raw HTML)
requires_tools: [run_python]
capabilities_needed: [requests, beautifulsoup4]
keywords: [web scraping, URL, HTML, text extraction, webpage, content parsing]
produces: [cleaned plain text of the webpage]
domain: literature
source: biomni:tool/literature.py::extract_url_content
---
# Extract URL Content

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. `requests.get(url, headers={"User-Agent": "Mozilla/5.0"})`.
2. If `Content-Type` is `text/plain` or `application/json`, return `response.text` directly.
3. Otherwise parse with `BeautifulSoup(response.text, "html.parser")`.
4. Find the main content container: prefer `<main>`, then `<article>`, then `<body>`.
5. Remove noise elements: `script`, `style`, `nav`, `header`, `footer`, `aside`, `iframe`.
6. Extract all `<p>`, `<h1>`–`<h6>` tags, strip whitespace, drop empty strings.
7. Return paragraphs joined with double newlines.

## Key decisions
- Prioritising `<main>` / `<article>` over full `<body>` reduces boilerplate navigation text.
- Early return for plain-text/JSON responses avoids unnecessary HTML parsing.

## Caveats
- JS-rendered pages (SPAs) will return little content; use a headless browser (playwright) for those.
- Very large pages may include a lot of text; consider truncating to a token budget before passing to an LLM.
- Some sites require authentication or set cookies on first visit; this simple fetch will fail there.

## In ABA
Implement with `run_python`; `ensure_capability("requests", "beautifulsoup4")`. Original impl: `source` -> lift to lakeFS later.
