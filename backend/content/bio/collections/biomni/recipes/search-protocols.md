---
name: search-protocols
description: Search the protocols.io public API for experimental protocols matching a keyword query.
when_to_use: When the user wants to find published experimental protocols by keyword (method name, reagent, technique) from the protocols.io repository.
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [protocols, protocols.io, experimental methods, lab protocol, search, CRISPR, cloning, assay]
produces: [list of protocol records with title, description, URL, DOI, authors, tags, peer-review status]
domain: protocols
source: biomni:tool/protocols.py::search_protocols
---
# Search Protocols

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Require a non-empty query string and a valid `PROTOCOLS_IO_ACCESS_TOKEN` environment variable.
2. GET `https://www.protocols.io/api/v3/protocols` with headers `Authorization: Bearer <token>` and params `filter=public`, `key=<query>`.
3. Check `data["status_code"] == 0`; if not, return the error message.
4. For each item in `data["items"]` extract: id, title, description, uri (URL), doi, authors (name + username), created_on, published_on, version_id, nr_of_views, is_peer_reviewed, tags.
5. Return a dict with keys `protocols`, `pagination` (current_page, total_pages, page_size, total_results), `total_results`, `status_code`.

## Key decisions
- The query is passed verbatim as `key`; protocols.io treats it as an exact-match keyword search.
- Token is read from env var `PROTOCOLS_IO_ACCESS_TOKEN` (or `BIOMNI_PROTOCOLS_IO_ACCESS_TOKEN`); never hardcoded.

## Caveats
- Pagination is not automatically iterated; caller must loop over pages using the returned `pagination` info if needed.
- The API requires a registered access token from protocols.io even for public protocol search.

## In ABA
Implement with `run_python`; `ensure_capability("requests")`; expose token via project env. Original impl: `source` -> lift to lakeFS later.
