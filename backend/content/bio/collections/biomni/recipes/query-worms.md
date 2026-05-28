---
name: query-worms
description: Query the World Register of Marine Species (WoRMS) for marine taxonomy and species records
when_to_use: When you need authoritative marine species names, AphiaIDs, synonyms, classification, or distribution data
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [worms, marine species, taxonomy, aphia, classification, synonyms, marine biology]
produces: [species records, AphiaID, taxonomic classification, synonyms]
domain: database
source: "biomni:tool/database.py::query_worms"
---
# Query WoRMS

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://www.marinespecies.org/rest`
2. No authentication required
3. Key endpoints:
   - Exact name lookup: GET `{base}/AphiaRecordByName/{scientific_name}?marine_only=true`
   - Fuzzy name search: GET `{base}/AphiaRecordsByName/{name}?fuzzy=true&marine_only=true`
   - By AphiaID: GET `{base}/AphiaRecordByAphiaID/{aphia_id}`
   - Classification: GET `{base}/AphiaClassificationByAphiaID/{aphia_id}`
   - Synonyms: GET `{base}/AphiaSynonymsByAphiaID/{aphia_id}`
   - Children taxa: GET `{base}/AphiaChildrenByAphiaID/{aphia_id}`
   - Distribution: GET `{base}/AphiaDistributionsByAphiaID/{aphia_id}`
4. Scientific names in URL must be URL-encoded (spaces as `%20`)
5. Response includes `AphiaID`, `scientificname`, `status`, `rank`, `valid_name`, `valid_AphiaID`
6. For multiple names, use POST `{base}/AphiaRecordsByNames` with JSON body `{"scientificnames": [...]}`

## Key decisions
- Always use scientific names (not common names) for precise lookups
- Check `status` field: `accepted` means valid; `unaccepted` means it's a synonym — follow `valid_AphiaID`
- Use `marine_only=true` to filter out freshwater/terrestrial homonyms

## Caveats
- Name matching is case-sensitive in strict mode; use fuzzy search for uncertain spellings
- AphiaID is the stable identifier; use it for all follow-up queries

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
