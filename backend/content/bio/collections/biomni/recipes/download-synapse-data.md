---
name: download-synapse-data
description: Download files, datasets, folders, or projects from Synapse by entity ID using the synapse CLI, with auto-install of synapseclient if absent.
when_to_use: When the agent needs to retrieve data from a Synapse repository by entity ID (syn...) before analysis.
requires_tools: [run_python]
capabilities_needed: [synapseclient]
keywords: [Synapse, synapseclient, data download, syn ID, dataset, bioinformatics data, TCGA, ENCODE, repository]
produces: [downloaded files in target directory, result dict with status and errors]
domain: support_tools
source: biomni:tool/support_tools.py::download_synapse_data
---
# Download Synapse Data

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. Check that `SYNAPSE_AUTH_TOKEN` is set in the environment; return an error dict if missing.
2. Auto-install synapseclient via pip if not importable.
3. Normalise `entity_ids` to a list; enforce single-entity constraint for datasets, folders, and projects.
4. Dispatch by `entity_type`:
   - `"file"`: call `synapse get <id> --downloadLocation <dir>` for each ID.
   - `"dataset"`: call `synapse get -r <id> --downloadLocation <dir>`.
   - `"folder"`: call `synapse get -r <id> --downloadLocation <dir>` (adds `--recursive` when `recursive=True`).
   - `"project"`: call `synapse get -r <id> --downloadLocation <dir>`.
   - Also supports `follow_link=True` which appends `--followLink`.
5. Run each subprocess with the given `timeout`; collect stdout/stderr.
6. Return a dict with keys `success`, `downloaded_files`, `errors`, `entity_type`.

## Key decisions
- entity_type must match the actual Synapse node_type from search results — the default "dataset" is not universal.
- Multiple entity IDs are only valid for type "file".
- Authentication is token-based via env var, not interactive login.

## Caveats
- Requires SYNAPSE_AUTH_TOKEN in the runtime environment.
- Large datasets may need a timeout increase beyond the 300 s default.
- Synapse quotas and data use agreements may apply to restricted datasets.

## In ABA
Implement with `run_python`; `ensure_capability("synapseclient")`. Set SYNAPSE_AUTH_TOKEN via project secrets before invocation. Original impl: `biomni:tool/support_tools.py::download_synapse_data` -> lift to lakeFS later.
