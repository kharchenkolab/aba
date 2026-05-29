---
name: query-reactome
description: Query the Reactome pathway database — map a gene to the pathways it participates in, fetch pathway/entity details, run pathway over-representation analysis, and export pathway diagrams
when_to_use: When retrieving biological pathway information, mapping a gene (e.g. "which pathways is EGFR in?") or a gene list to Reactome pathways, running pathway enrichment, or downloading pathway diagrams
requires_tools: [run_python]
capabilities_needed: [requests]
keywords: [Reactome, pathway, biological process, gene, gene symbol, UniProt, R-HSA, ContentService, AnalysisService, enrichment, over-representation, diagram]
produces: [pathway records, gene-pathway mappings, enrichment results, diagram PNG files]
domain: database
source: "Reactome ContentService + AnalysisService REST API — reactome.org/dev"
---
# Query Reactome

Reactome exposes two REST services with **different base URLs** — pick by task:
- **ContentService** (`https://reactome.org/ContentService`) — retrieve specific
  entities, map an identifier to its pathways, export diagrams. No POST/token.
- **AnalysisService** (`https://reactome.org/AnalysisService`) — submit a gene
  list, get pathway **over-representation** (enrichment) ranked by p-value.

**Provision:** `ensure_capability("requests")`, then everything below in `run_python`.

## Gene → pathways: pick the right path (this is the bug the old recipe hit)
"Which pathways is EGFR in?" is **not** a text search. Searching
`search/query?query=EGFR` only returns entities whose *name* contains "EGFR"
(e.g. "Signaling by EGFR") and misses the dozens of pathways EGFR participates in
without being named. Use one of these two membership paths instead:

### Path A (recommended) — AnalysisService `/identifiers/`, gene symbol works directly
Reactome's FAQ recommends the **HGVS gene name or UniProt accession** as the
identifier; the AnalysisService accepts a bare gene symbol (one per line) as the
POST body and maps it for you. This is also the right call for a gene *list*
(it returns proper over-representation statistics).
```python
import requests
ANALYSIS = "https://reactome.org/AnalysisService"
genes = "EGFR"                       # or "\n".join(["EGFR","KRAS","TP53"]) for a list
r = requests.post(
    f"{ANALYSIS}/identifiers/projection",   # /projection maps non-human orthologs too; drop it for strict human
    params={"interactors": "false", "species": "Homo sapiens",
            "pageSize": 50, "page": 1, "sortBy": "ENTITIES_PVALUE", "order": "ASC"},
    headers={"Content-Type": "text/plain", "Accept": "application/json"},
    data=genes)
r.raise_for_status()
res = r.json()
assert res["identifiersNotFound"] == 0, f"unmapped: {res['identifiersNotFound']}"
token = res["summary"]["token"]               # reuse to fetch details/diagrams for THIS result
for p in res["pathways"]:                      # each: stId (R-HSA-…), name, entities{pValue,fdr,found,total}
    print(p["stId"], p["name"], p["entities"]["fdr"])
```
For EGFR this returns real pathways — *Signaling by EGFR*, *EGFR interacts with
phospholipase C-gamma*, *Signaling by Overexpressed Wild-Type EGFR in Cancer*,
*GRB2 events in EGFR signaling*, etc. (`/identifiers/` without `/projection`
finds EGFR in ~111 pathways; tune `pageSize`/`sortBy` as needed).

### Path B — low-level membership via ContentService `data/mapping`
Direct "what pathways contain this molecule" lookup, **no enrichment stats**. The
mapping endpoint needs a UniProt accession, so resolve the symbol first (see the
`query-uniprot` recipe): EGFR (human) → `P00533`.
```python
CONTENT = "https://reactome.org/ContentService"
acc = "P00533"                                  # UniProt accession for EGFR_HUMAN
r = requests.get(f"{CONTENT}/data/mapping/UniProt/{acc}/pathways",
                 params={"species": "9606"}, headers={"Accept": "application/json"})
r.raise_for_status()
for p in r.json():                              # list of pathway objects: stId, displayName, hasDiagram
    print(p["stId"], p["displayName"])
```
Returns ~37 pathways for EGFR — *Signaling by EGFR* (R-HSA-177929), *GRB2 events
in EGFR signaling*, *GAB1 signalosome*, *PIP3 activates AKT signaling*, etc.
(Resource segment can also be `Ensembl`, `NCBI Gene`, `ChEBI`; UniProt is the
most reliable.)

## ContentService — entity retrieval & search
```python
CONTENT = "https://reactome.org/ContentService"
# Full record for a known stable ID:
requests.get(f"{CONTENT}/data/query/R-HSA-177929").json()        # displayName, schemaClass, …
# Sub-pathways / contained events:
requests.get(f"{CONTENT}/data/pathway/R-HSA-177929/containedEvents").json()
# Text search SCOPED to pathways (use ONLY when you want name matches, not membership):
requests.get(f"{CONTENT}/search/query",
             params={"query": "EGFR", "species": "Homo sapiens", "types": "Pathway"}).json()
```

## Export a pathway diagram (PNG)
The current exporter is `exporter/diagram/{stId}.{ext}` on **ContentService**
(the old `data/pathway/{id}/diagram` route is gone — that was part of the bug):
```python
import os
stId = "R-HSA-177929"
img = requests.get(f"{CONTENT}/exporter/diagram/{stId}.png", params={"quality": 7})
img.raise_for_status()                           # 200, content-type image/png
out = os.path.join(os.environ["ARTIFACTS_DIR"], f"{stId}.png")
with open(out, "wb") as fh: fh.write(img.content)
# `.svg`/`.pdf` also supported; `exporter/fireworks/...` for the whole-genome view.
```

## Key decisions
- **Gene→pathways = membership, not name search.** Use AnalysisService
  `/identifiers/` (Path A) or ContentService `data/mapping` (Path B). Reserve
  `search/query` for "find a pathway whose name mentions X".
- AnalysisService for a **gene list / enrichment** (ranked by p-value/FDR);
  ContentService `data/mapping` for a plain **membership list** with no stats.
- Human pathway stable IDs start with `R-HSA-`; pass `species=Homo sapiens`
  (Analysis) or `species=9606` (mapping) to stay human-only.
- Reactome recommends the **HGVS gene symbol or UniProt accession** as the input
  identifier; gene symbols work directly in AnalysisService `/identifiers/`.

## Caveats
- Two different base URLs — ContentService vs AnalysisService; don't cross them.
- AnalysisService is POST with `Content-Type: text/plain` and the identifiers as
  the raw body (newline-separated), **not** JSON or query params.
- Always check `identifiersNotFound` / `pathwaysFound` before trusting the result —
  a typo'd symbol silently maps to nothing (this is how the old recipe "returned 0").
- The analysis `token` expires; re-POST to regenerate. `/projection` lifts
  non-human identifiers to human pathways — drop it for strict-human analyses.
- Some pathways lack a diagram (`hasDiagram: false`) — check before exporting.

## In ABA
Implement with `run_python` and `requests`; `ensure_capability("requests")`. To
resolve a gene symbol → UniProt accession for Path B, see the `query-uniprot`
recipe. Save diagram PNGs under `ARTIFACTS_DIR`.
