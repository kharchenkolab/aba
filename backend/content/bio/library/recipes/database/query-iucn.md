---
name: query-iucn
description: Query the IUCN Red List API for species conservation status and threat data
when_to_use: When you need conservation status, threat categories, population trends, or habitat data for species
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [iucn, red list, conservation, endangered, species, threatened, habitat, biodiversity]
produces: [conservation status, threat category, population trend, species records]
domain: database
source: "biomni:tool/database.py::query_iucn"
---
# Query IUCN Red List

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://apiv3.iucnredlist.org/api/v3`
2. All requests require `?token={token}` query parameter (free registration at iucnredlist.org)
3. Key endpoints:
   - Species by name: GET `{base}/species/{scientific_name}?token={token}`
   - Species by ID: GET `{base}/species/id/{id}?token={token}`
   - Conservation measures: GET `{base}/measures/species/name/{name}?token={token}`
   - Threats: GET `{base}/threats/species/name/{name}?token={token}`
   - Habitats: GET `{base}/habitats/species/name/{name}?token={token}`
   - Species by region: GET `{base}/species/region/{region_identifier}/page/{page}?token={token}`
   - Search: GET `{base}/species/search?query={query}&token={token}`
4. Response JSON has a `result` array; IUCN category codes: LC, NT, VU, EN, CR, EW, EX
5. Prefer scientific names over common names for precision

## Key decisions
- Never log or return the token in results; redact it from any recorded URLs
- Use species ID (numeric) for unambiguous queries when available

## Caveats
- Requires a valid API token; requests without token return 401
- Regional assessments may differ from global status
- Data reflects the assessment year; check `assessment_date` field

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Store token in env var `IUCN_API_TOKEN`. Original impl: `source` -> lift to lakeFS later.
