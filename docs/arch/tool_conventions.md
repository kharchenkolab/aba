# Tool conventions

Canonical naming + parameter conventions for the agent-visible `aba_core` tool
catalog. The objective is **systematic + predictable**: an agent (and a human)
should be able to guess a tool's name and its parameter names from the intent,
because the catalog follows one vocabulary everywhere.

This is a **shared agent input** (per the repo's change-discipline rule): a change
here has platform-wide blast radius. `tests/test_tool_conventions.py` enforces the
rules below against the live catalog; the `regtest/placement` sweep is the
behavioral guard for any tool/param that agents actually call.

Guiding principle from the tool-catalog investigation (see `project-tool-catalog`):
the catalog is prompt-cached (~84% hit), so **tool count is not the per-turn cost** —
these conventions optimize for *predictability/discoverability*, not fewer tokens.

## Verb vocabulary (tool-name prefix → intent)

One prefix per intent; boundaries are crisp so there's exactly one right verb.

| prefix | intent | examples |
|---|---|---|
| `search_` | ranked find in a registry/catalog (by corpus) | `search_skills` (recipes), `search_capabilities` (curated catalog), `search_registry(source=…)` (external) |
| `list_` | enumerate project-local items (filter, not rank) | `list_entities`, `list_revisions`, `list_data_files`, `list_entity_operations` |
| `get_` | return a **computed/derived** value | `get_lineage`, `get_job_status` |
| `read_` | return **stored content verbatim** (file bytes, entity fields, memory) | `read_file`, `read_entity`, `read_memory` |
| `describe_` | structured human-readable detail of an **external/system** thing | `describe_tool`, `describe_compute`, `describe_pipeline`, `describe_reference` |
| `inspect_` | probe **by running** something (import a pkg, sniff a file, diagnose env) | `inspect_package`, `inspect_env`, `inspect_upload` |
| `create_` / `register_` / `open_` / `make_` / `propose_` | create a new entity/thing | `create_finding`, `register_dataset`, `open_run`, `make_revision`, `propose_capability` |
| `update_` / `set_` | modify existing state | `update_entity_fields`, `set_active_env`, `set_current_revision` |
| `run_` | execute code/a pipeline | `run_python`, `run_r`, `run_nextflow` |
| `fetch_` | download external data into the workspace/store | `fetch_url`, `fetch_ensembl`, `fetch_reference` |
| `view_` | inject visual/vision content into context | `view_artifact`, `view_file` |

Boundary notes that resolve the `read`/`get`/`describe`/`inspect` overlap:
- `read_` = stored bytes/fields returned as-is. `get_` = a value we *compute* (a graph
  walk, a status). `describe_` = a summary of a system/external object. `inspect_` =
  we *execute* to find out (loads a module, sniffs a file).

## Parameter-name map (one name per concept)

| concept | canonical param | notes / disallowed variants |
|---|---|---|
| the entity to act on | `entity_id` | NOT `figure_id`. Typed ids are allowed **only** where the type genuinely constrains input: `result_id`, `dataset_id`, `reference_id`, `exec_id`, `member_id`, `job_id`. |
| a file to read/act on | `path` | NOT `file_path`, NOT `filename` (for an input path). A *destination/output* name is a different concept — name it `dest` or `out_name`, not `filename`. |
| a free-text search string | `query` | NOT a second `name` param meaning the same thing. |
| an entity/memory type | `type` | NOT `entity_type`. |
| result-count cap on a list/search | `limit` | NOT `max_results`. (`max_depth` = graph depth, a different concept — allowed.) |
| a programming language | `language` | typed `Literal["python","r"]` where feasible. |
| the text/code/body payload | `body` (files/memory), `code` (exec), or a **semantic** name (`interpretation`, `statement`, `text`) where the field has domain meaning. |

A tool must not expose **two params for one concept** (e.g. `search_pypi(query, name)`,
`read_capability(name, capability)`) — pick the canonical one.

## Keep-list — deliberate exceptions (do NOT "fix")

- **`Skill`** — CapitalCase, native Claude affordance the model is trained to emit; sole recipe-invocation verb.
- **`run_python` / `run_r`** — two tools, not `run(language=)`: language-specific install/kernel prose is load-bearing.
- **`present_plan` / `ask_clarification`** — loop-suspending control tools intercepted before dispatch; keep names + typed `steps` contract.
- **`view_artifact` / `view_file`** — carry the vision channel (`_vision_blocks`); the tool identity is a side-channel, not just data.
- **`describe_tool`** — the discoverability escape hatch; always standalone.
- Typed ids (`result_id`/`dataset_id`/`reference_id`/…) — kept where the type constrains the argument.

## De-duplication (what merged, what deliberately did NOT)

Merges applied (real redundancy — identical or coherently-unified behavior):
- `get_provenance` + `get_dependents` → **`get_lineage(entity_id, direction='up'|'down'|'both')`** (identical signature; direction is a natural param).
- `search_pypi` + `search_bioconda` + `search_nf_core` + `search_mcp_registry` → **`search_registry(query, source='pypi'|'bioconda'|'nf_core'|'mcp')`**. `search_skills` (recipe library) and `search_capabilities` (curated catalog) stay SEPARATE — different corpora, different intents.
- `read_skill` dropped (deprecated alias of `Skill`).

Deliberately NOT merged (judgment: distinct behavior, not redundancy — merging would bury the distinction in a param, the fat-`op=` anti-pattern):
- **`view_file` vs `view_artifact`** — `view_artifact` renders a PDF page as an *image* (vision-first, to verify figures); `view_file` extracts PDF *text* + hex-dumps binaries (read-first). Different intents.
- **`find_reference` vs `describe_reference`** — a legitimate `list_`/`describe_` pair (search-by-facets vs detail-by-id), not a duplicate.
- **`annotate_entity` vs `update_entity_fields`** — kept `annotate_entity`: its flat named fields (`tags`/`notes`/`title`/`status`) are more discoverable for the common curation case than a free-form `fields={}` dict. Count is cached, so the extra tool costs ~nothing.

Principle: **merge only genuine redundancy; keep distinct behaviors explicit.** Tool count is not the cost (the catalog is cached); discoverability is.

## Functional clusters

The catalog groups into these functional clusters (the durable organization —
independent of which `tools/*.py` module currently registers each, which does NOT
match cluster boundaries and is a known code-hygiene smell left for a later pass;
the physical file moves are churn with no agent-facing benefit, so they were
deliberately deferred rather than done under this reorg).

| cluster | tools |
|---|---|
| entity reads | `list_entities`, `read_entity`, `list_entity_operations`, `get_lineage`, `read_csv_info` |
| entity writes / curation | `promote_to_result`, `create_finding`, `create_claim`, `annotate_entity`, `update_entity_fields`, `update_member_caption`, `add_result_member`, `remove_result_member`, `archive_entity` |
| datasets / uploads | `register_dataset`, `add_to_dataset`, `remove_from_dataset`, `check_import`, `inspect_upload`, `list_data_files` |
| runs | `open_run`, `close_run` |
| references | `register_reference`, `find_reference`, `fetch_reference`, `resolve_reference`, `promote_reference`, `describe_reference` |
| discovery / search | `search_skills`, `search_registry`, `search_capabilities`, `read_capability`, `describe_tool`, `inspect_package` |
| environment | `ensure_capability`, `propose_capability`, `inspect_env`, `make_isolated_env`, `set_active_env` |
| external fetch | `fetch_url`, `fetch_ensembl`, `lookup_sra_runinfo` |
| skills | `Skill` |
| files | `read_file`, `write_file`, `edit_file`, `find_files`, `view_file` |
| exec | `run_python`, `run_r`, `restart_kernel` |
| jobs / compute | `describe_compute`, `get_job_status`, `cancel_job` |
| pipelines | `run_nextflow`, `describe_pipeline`, `import_run` |
| revisions / reproduction | `make_revision`, `list_revisions`, `set_current_revision`, `delete_revision`, `reproduce_from_exec`, `diff_env`, `rebuild_env`, `export_reproduction_bundle`, `pin_cell` |
| viewers | `view_artifact`, `get_viewer_url` |
| memory | `read_memory`, `write_memory` |
| feedback | `read_aba_logs`, `read_client_context`, `build_bug_report` |
| plan / control | `present_plan`, `ask_clarification` |

## Enforcement

- **Structural:** `tests/test_tool_conventions.py` enumerates the live catalog and
  asserts every tool's verb prefix + param names conform (allowlist for the keep-list).
  New tools that violate fail CI.
- **Behavioral:** `regtest/placement` (standard tier / opus) for any agent-facing
  rename or param change — tool-argument correctness must not regress.
- **Renames** update the `aba-recipe-pack` references in lockstep (we control the pack;
  no long-lived aliases).
