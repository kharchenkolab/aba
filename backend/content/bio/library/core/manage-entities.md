---
name: manage-entities
description: Operate the project's own entities — register datasets, pin, promote figures to results, build findings and claims, tag/annotate — the same actions a user can take in the UI.
when_to_use: Whenever the user refers to the project's structured objects — "add/register this as a dataset", "pin that figure", "this is a result/finding", "make that a claim", "tag/rename this". These are first-class actions, not file operations.
requires_tools: [list_entities, register_dataset, pin_entity, promote_to_result, create_finding, create_claim, annotate_entity]
keywords: [dataset, register dataset, pin, promote, result, finding, claim, evidence, tag, annotate, entity, workspace, provenance]
---

# Managing project entities

This project is an entity graph, and you can operate it directly — anything the
user can do in the UI, you can do too. **Do not** substitute file-dumping or a
"here are your options" menu when the user asks for one of these actions. Just
do it, then confirm.

## The entity model

```
dataset ──► figure / table ──► result ──► finding ──► claim
                                              ▲
                                          narrative, note (free-standing)
```

- **dataset** — a registered data file/bundle (counts, .h5ad, 10x dir). Lives in the Data facet; feeds analyses.
- **figure / table** — produced outputs (plots auto-register from run_python).
- **result** — an *interpreted* observation; a figure is its evidence.
- **finding** — a synthesis backed by one or more results.
- **claim** — a stated assertion the evidence supports (or refutes).

## Intent → tool

| User says | Do |
|---|---|
| "add / register this as a dataset" | `register_dataset(path, title, source=…, producing_code=…)` — **always**, the moment data is fetched/built. Capture provenance (e.g. `source="GEO:GSM5746259"` and the fetch code). |
| "pin this" / "keep this up" | `pin_entity(entity_id)` (`pinned=false` to unpin) |
| "this plot shows X" / "promote this" | `promote_to_result(figure_id, interpretation)` |
| "together these show…" | `create_finding(result_ids, text)` |
| "so we can claim…" | `create_claim(statement, evidence_ids=…)` |
| "tag / rename / note this" | `annotate_entity(entity_id, tags/notes/title/status)` |
| "what figures/results/datasets do I have?" | `list_entities(type=…)` |

## Two rules

1. **You need the id.** Most ops take an `entity_id` — call `list_entities` (filter by `type`, `query`) to find it before pinning/promoting/citing. The current focus entity is already in your context.
2. **Capture provenance on registration.** A dataset without its `source` and `producing_code` is an orphan — always pass them so the graph stays traceable.
