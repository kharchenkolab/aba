---
name: query-paleobiology
description: Query the Paleobiology Database (PBDB) for fossil occurrence records and taxonomic data
when_to_use: When you need fossil occurrence data, paleontological taxonomy, or biogeographic range information across geological time
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [paleobiology, fossil, paleontology, occurrence, taxon, stratigraphy, PBDB, geological time]
produces: [fossil occurrences, taxonomic records, stratigraphic data, paleogeographic data]
domain: database
source: "biomni:tool/database.py::query_paleobiology"
---
# Query Paleobiology Database

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Base URL: `https://paleobiodb.org/data1.2`
2. URL pattern: `{base}/{resource}/{method}.{format}?{params}`
3. Key resources and methods:
   - Occurrences: `occs/list.json?name={taxon}&show=paleoloc,phylo`
   - Taxa: `taxa/list.json?name={taxon}&rel=all_children`
   - Single taxon: `taxa/single.json?name={taxon}`
   - Collections: `colls/list.json?taxon_name={taxon}&interval={time_interval}`
   - Time intervals: `intervals/list.json?name={interval_name}`
4. Supported formats: `.json`, `.csv`, `.tsv`, `.txt`
5. Useful params:
   - `name`: taxon name (scientific)
   - `interval`: geological time interval (e.g. `Cretaceous`, `Maastrichtian`)
   - `cc`: country code for geographic filtering
   - `lngmin/lngmax/latmin/latmax`: bounding box
   - `vocab=pbdb` (verbose) or `vocab=com` (compact field names)
   - `show=paleoloc,phylo`: include paleolocation and phylogenetic data
6. Image endpoints end in `.png`; handle binary separately

## Key decisions
- Use standard geological time names for interval queries
- Include `show=paleoloc` for occurrences to get paleo-coordinates

## Caveats
- No authentication required; publicly accessible
- Large taxon queries can return many thousands of occurrences; filter by time or geography
- Paleocoordinates are model-reconstructed and have uncertainty

## In ABA
Implement with `run_python`; `ensure_capability("requests")`. Original impl: `source` -> lift to lakeFS later.
