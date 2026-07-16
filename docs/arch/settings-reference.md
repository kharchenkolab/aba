# Settings reference

> **Generated** from the `core/config.py` settings registry (`list_settings()`), the
> single enforced read path for every `ABA_*` var the backend consumes. Regenerate with
> `scripts/gen_settings_reference.py` (or view live with `aba settings`). Do not hand-edit.

Each setting is declared once via `setting()` and read through `config.settings.<name>.get()`. Columns:

- **env** — the environment variable(s) read (first present wins).
- **type** / **default** — coercion + value when unset (deployment-neutral).
- **weft_fate** — what the future weft compute-substrate rewrite does with it (`keep` / `retire` / `move:site` / `move:envspec` / `revisit`).
- **reduction** — the fewer-better-variables plan (`keep` / `dead` / `resolve-flag` / `merge:<group>` / `derive:<from>` / `relocate:<layer>`).
- **flags** — `branches` (changes behavior), `secret` (redacted), `deploy` (launcher-forwarded / `deploy_injected`).

**124 settings** across 11 categories.  
weft_fate — `keep` 66, `move:envspec` 9, `move:site` 21, `retire` 22, `revisit` 6.  
reduction — `derive` 1, `keep` 70, `merge` 50, `relocate` 2, `resolve-flag` 1.

### Tag vocabularies

**`weft_fate`** — what the planned [weft](https://github.com/kharchenkolab/weft) compute-substrate migration does with a setting (advisory metadata; changes nothing today):

- `keep` — aba-native, survives weft (model/LLM, behavior flags, paths aba keeps).
- `retire` — weft owns the concern; the var goes away (placement, offload, most nextflow).
- `move:site` — becomes weft **site-registration** config (SIF/module/bind/accelerator wiring).
- `move:envspec` — becomes part of a weft **EnvSpec** (tools dir, base lock, R repos/pins).
- `revisit` — genuinely ambiguous; decide at weft time (kernel lifecycle, prewarm).

**`reduction`** — the fewer-better-variables plan (each is a guarded, reviewable cut once the anti-bypass guard proves read-site completeness):

- `keep` — genuinely a per-process env knob; leave it.
- `dead` — read but effectively never set / feature gone → delete.
- `resolve-flag` — a feature flag that has won (→ always-on, drop the flag) or lost (→ delete with its branch).
- `merge:<group>` — fold a family of flat vars into one structured setting (surface, not count).
- `derive:<from>` — compute the value instead of configuring it (drop the knob).
- `relocate:<layer>` — move a mis-homed knob to where it's curated (bundle `settings.yaml`, `hpc.yaml`, user prefs).

## paths

*Filesystem roots and directories.*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `artifacts_dir` | `ARTIFACTS_DIR` | path | /users/peter.kharchenko/.aba/runtime/projects/_workspace/artifacts | keep | keep |  | Workspace-level artifacts dir (no-project fallback). |
| `data_dir` | `DATA_DIR` | path | /users/peter.kharchenko/.aba/runtime/projects/_workspace/data | keep | keep |  | Workspace-level data dir (no-project fallback). |
| `envs_dir` | `ABA_ENVS_DIR` | path | /users/peter.kharchenko/.aba/runtime/envs | move:envspec | keep | deploy | Materialized-tools area (conda envs + pylib overlay); wipeable whole. |
| `frontend_dist` | `ABA_FRONTEND_DIST` | str |  | keep | keep | deploy | Built frontend dist dir served by the backend. |
| `home_dir` | `ABA_HOME` | str |  | keep | keep | deploy | Install home ($ABA_HOME): config.env, oauth store, vendor. None → ~/.aba. |
| `pagoda3_dist` | `ABA_PAGODA3_DIST` | str |  | move:site | keep |  | pagoda3 viewer dist dir (else derived under $ABA_HOME/vendor). |
| `projects_dir` | `ABA_PROJECTS_DIR` | path | /users/peter.kharchenko/.aba/runtime/projects | keep | keep |  | Per-project consolidated roots (projects/<pid>/). |
| `raw_request_dir` | `ABA_RAW_REQUEST_DIR` | str | /tmp/aba_llm_sent | keep | keep |  | Debug dump dir for raw LLM requests (diagnostics only). |
| `refs_dir` | `ABA_REFS_DIR` | path | /users/peter.kharchenko/.aba/runtime/refs | keep | keep |  | Content-addressed shared reference store (genomes, indices, annotations). |
| `refsources_dir` | `ABA_REFSOURCES_DIR` | str |  | move:site | keep |  | Override for the reference-sources catalog dir. |
| `release_id` | `ABA_RELEASE_ID` | str |  | move:site | keep | deploy | Active release id under $ABA_SHARE/releases (else resolve_current()). |
| `runtime_dir` | `ABA_RUNTIME_DIR` | path | /users/peter.kharchenko/.aba/runtime | keep | keep | deploy | Root for all mutable runtime state (projects, envs, refs, workspace DB). |
| `share_dir` | `ABA_SHARE` | str |  | move:site | keep | deploy | Shared install tree ($ABA_SHARE) for immutable releases; unset on personal/slim. |
| `tools_dir` | `ABA_TOOLS_DIR` | str |  | move:envspec | keep |  | Override for the materialized-tools dir (else derived under ENVS_DIR). |
| `turn_log_dir` | `ABA_TURN_LOG_DIR` | str | /tmp/aba_turnlog | keep | keep |  | Directory for per-turn structured logs. |
| `work_dir` | `ABA_WORK_DIR` | path | /users/peter.kharchenko/.aba/runtime/projects/_workspace/work | keep | keep |  | Workspace-level work dir (no-project fallback). |

## deploy

*Container / offload / module wiring injected by the installer or OOD launcher.*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `accelerator` | `ABA_ACCELERATOR` | str |  | move:site | derive:gpu-probe | branches | Accelerator hint ('cuda' → CUDA-aware paths); else CPU / probe-derived. |
| `apptainer_tmpdir` | `ABA_APPTAINER_TMPDIR` | str |  | move:site | keep | deploy | TMPDIR for apptainer/singularity build+run scratch. |
| `job_wrap` | `ABA_JOB_WRAP` | str |  | move:site | keep | branches deploy | Job wrapper mode ('sif' → run jobs via apptainer exec <SIF>). |
| `lmod_init` | `ABA_LMOD_INIT` | str |  | move:site | keep |  | Lmod init script path (else from site config init_path). |
| `module_binds` | `ABA_MODULE_BINDS` | str |  | move:site | keep | deploy | Space-separated bind mounts injected when wrapping jobs in the SIF. |
| `module_init` | `ABA_MODULE_INIT` | str |  | move:site | keep | deploy | Lmod init snippet path for module-based nextflow/tool execution. Forwarded into the SIF so an offloaded bare job (nf-core head) can re-init the site's module system on its compute node. |
| `modules_eager` | `ABA_MODULES_EAGER` | str |  | move:site | keep |  | Eagerly materialize module manifests at startup (fat-SIF baked artifacts). |
| `modules_enabled` | `ABA_MODULES_ENABLED` | str |  | move:site | keep | branches | '0' disables the environment-modules integration. |
| `pixi_bin` | `ABA_PIXI_BIN` | str |  | keep | keep |  | Path to the pixi binary weft solves/realizes with. None → $PATH lookup, then $ABA_HOME/tools/pixi/bin/pixi. |
| `sif` | `ABA_SIF` | str |  | move:site | keep | deploy | Path to the fat/slim SIF image used to wrap jobs. |
| `weft_publish_site` | `ABA_WEFT_PUBLISH_SITE` | str | local | keep | keep |  | Site whose realization store backs the published catalog (where env_adopt runs). |
| `weft_publish_staging` | `ABA_WEFT_PUBLISH_STAGING` | str |  | keep | keep |  | Where a publish's build churn lands (weft env_publish `staging`): None → weft 'auto' (under the site root); an absolute node-local path (e.g. /dev/shm/pubstage) is fastest on a netfs tree — the slow tree then gets one sequential image write instead of ~10^4 small-file ops. |
| `weft_publish_tree` | `ABA_WEFT_PUBLISH_TREE` | str |  | keep | keep | deploy | Published base-env catalog tree (shared read-only folder). When set, base packs ADOPT from it by name (no solve); unset → solve locally. Admin seeds it with core.compute.seeding. |
| `weft_sites` | `ABA_WEFT_SITES` | str |  | keep | keep |  | Deployment site declarations (weft-sites.yaml: non-local weft sites — slurm/ssh). None → $ABA_HOME/weft-sites.yaml. |
| `weft_workspace` | `ABA_WEFT_WORKSPACE` | str |  | keep | keep |  | weft workspace dir (holds .weft state + the local site root). None → $ABA_HOME/weft. One workspace per deployment; per-project identity stays in the waist, not in weft. |

## mode

*Whole-process modes (SINGLE DB, runtime backend, tool kill-switch).*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `db_path` | `ABA_DB_PATH` | str |  | keep | keep | branches | Explicit workspace DB path → SINGLE mode (tests / single-user / e2e harness). The former ABA_DB_PATH_OVERRIDE alias was merged into this (env_reorg §6 reduction). |
| `disabled_tools` | `ABA_DISABLED_TOOLS` | csv | () | keep | keep | branches | Comma-separated global tool kill-switch (layered under agent allowlists). |
| `fake_session` | `ABA_FAKE_SESSION` | str |  | keep | keep | branches | Non-empty → deterministic fake LLM session (tests / demos). |
| `runtime_override` | `ABA_RUNTIME_OVERRIDE` | str |  | keep | keep | branches | Force the LLM runtime backend for the process (direct/sdk/fake/openai). |
| `settings_strict` | `ABA_SETTINGS_STRICT` | bool | False | keep | keep | branches | Any value → validate_settings() RAISES on out-of-enum/coerce-fail at startup instead of warning (CI / hardened deploys). |
| `version` | `ABA_VERSION` | str | dev | keep | keep |  | Deployed ABA version label (provenance stamp). |

## model

*Model selection + LLM request shape (Reasoning plane; aba-owned).*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `anthropic_api_key` | `ANTHROPIC_API_KEY` | str | •redacted• | keep | keep | secret | Anthropic API key — module-load snapshot (live reads go via core.llm). |
| `max_tokens` | `ABA_MAX_TOKENS` | int | 16000 | keep | keep |  | Max output tokens for primary LLM calls. |
| `model_snapshot` | `ABA_MODEL` | str | claude-haiku-4-5-20251001 | keep | merge:model | branches deploy | Process-startup model snapshot; last-resort fallback in the model resolver. |
| `openai_enable_thinking` | `ABA_OPENAI_ENABLE_THINKING` | str |  | keep | merge:openai |  | Opt into 'thinking' for the OpenAI runtime (1/true/yes/on). |
| `openai_model` | `ABA_OPENAI_MODEL` | str |  | keep | merge:openai |  | Default model for the OpenAI-compatible runtime (else caller default). |
| `openai_tool_result_framing` | `ABA_OPENAI_TOOL_RESULT_FRAMING` | str | none | keep | merge:openai |  | How tool results are framed for the OpenAI runtime. |
| `primary_model` | `ABA_PRIMARY_MODEL` `ABA_MODEL` | str |  | keep | merge:model | branches | Targeted primary-model override (ABA_PRIMARY_MODEL, else ABA_MODEL). |
| `primary_spec` | `ABA_PRIMARY_SPEC` | str |  | keep | keep |  | Force a specific agent spec for the primary lane. |
| `summary_model` | `ABA_SUMMARY_MODEL` | str | claude-haiku-4-5-20251001 | keep | merge:model |  | Model used for Tier-2 history summarization. |

## credentials

*Credential inputs + provider config (secrets redacted).*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `llm_credential` | `ABA_LLM_CREDENTIAL` | str |  | keep | keep |  | Credential MODE selector (e.g. 'oauth_cc'/'apikey') — not the secret itself. |
| `openai_account_id` | `ABA_OPENAI_ACCOUNT_ID` | str |  | keep | merge:openai |  | ChatGPT-Account-Id for the Codex subscription backend. |
| `openai_api_key` | `ABA_OPENAI_API_KEY` | str | •redacted• | keep | merge:openai | secret | OpenAI-compatible API key (ABA-scoped). |
| `openai_base_url` | `ABA_OPENAI_BASE_URL` | str |  | keep | merge:openai |  | OpenAI-compatible base URL (else provider default). |
| `subscription_oauth` | `ABA_SUBSCRIPTION_OAUTH` | str |  | keep | keep | deploy | Gates the subscription (Claude.ai/Codex) sign-in flow. |

## behavior

*Feature flags / behavior toggles.*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `advisors_enabled` | `ABA_ADVISORS_ENABLED` | bool | False | keep | keep | branches | Enable the advisor sub-agents pass. |
| `capability_approval` | `ABA_CAPABILITY_APPROVAL` | str | auto | keep | keep | branches | 'auto' publishes proposed capabilities immediately; 'ask' holds for review. |
| `data_summary` | `ABA_DATA_SUMMARY` | str | on | keep | keep | branches | Inject the data-summary prompt block; 'off' disables it. |
| `debug_timing` | `ABA_DEBUG_TIMING` | bool | False | keep | keep |  | Emit per-stage timing diagnostics. |
| `discovery_env_gate` | `ABA_DISCOVERY_ENV_GATE` | str |  | keep | relocate:userpref | branches | Env-gate for capability discovery (also a user preference). |
| `env_prewarm` | `ABA_ENV_PREWARM` | str | eager | revisit | keep | branches | Environment prewarm policy ('eager'/'lazy'/…). |
| `feed_log` | `ABA_FEED_LOG` | str | on | keep | keep |  | Feedback event logging; 'off' disables it. |
| `kernel_enabled` | `ABA_KERNEL_ENABLED` | bool | True | revisit | keep | branches | Master switch for the interactive kernel lane; off → stateless one-shot exec. |
| `preexec_veto` | `ABA_PREEXEC_VETO` | str | on | keep | keep | branches | Pre-exec safety veto; 'off' disables it. |
| `prompt_arm` | `ABA_PROMPT_ARM` | str | control | keep | keep | branches | A/B prompt arm selector (default 'control'). |
| `recovery_disabled` | `ABA_RECOVERY_DISABLED` | bool | False | keep | keep | branches | Any value disables the scribe recovery journal. |
| `weft_kernels` | `ABA_WEFT_KERNELS` | bool | False | keep | keep | branches | Route the interactive kernel through weft kernel_* (WeftKernelSession) instead of jupyter_client. |

## experimental

*Experimental gates (Phase-7 resolve-flag candidates).*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `experimental_ablate_blocks` | `ABA_EXPERIMENTAL_ABLATE_BLOCKS` | csv | () | keep | keep | branches | Debug/regression knob (off by default): comma-separated system-prompt block names to drop, on top of the mode's built-in drops. |
| `experimental_fetch_recipe` | `ABA_EXPERIMENTAL_FETCH_RECIPE` | bool | False | keep | resolve-flag | branches | Experimental: fetch-recipe discovery path. |

## tuning

*Numeric / non-behavioral tuning knobs.*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `cpu_limit` | `ABA_CPU_LIMIT` | str |  | move:site | keep |  | Override detected CPU limit (else cgroup/os probe). |
| `feedback_email` | `ABA_FEEDBACK_EMAIL` | str | pk.restricted@gmail.com | keep | keep |  | Destination address for in-app feedback. |
| `history_k_text_keep` | `ABA_HISTORY_K_TEXT_KEEP` | int | 12 | keep | merge:history |  | Layer-A window: number of recent text turns kept verbatim. |
| `history_k_tool_keep` | `ABA_HISTORY_K_TOOL_KEEP` | int | 30 | keep | merge:history |  | Layer-A window: number of recent tool_result blocks kept verbatim. |
| `history_summary_threshold_chars` | `ABA_HISTORY_SUMMARY_THRESHOLD_CHARS` | int | 400000 | keep | merge:history |  | Layer-B trigger: summarize when pruned history still exceeds this many chars. |
| `import_harvest_cap` | `ABA_IMPORT_HARVEST_CAP` | int | 40 | keep | keep |  | Max symbols harvested from an import for the tool catalog. |
| `kernel_cancel_grace_s` | `ABA_KERNEL_CANCEL_GRACE_S` | float | 3.0 | revisit | merge:kernel |  | Grace period (s) before force-killing a cancelled kernel cell. |
| `kernel_idle_ttl_s` | `ABA_KERNEL_IDLE_TTL_S` | int | 3600 | revisit | merge:kernel |  | Idle kernel time-to-live in seconds before LRU eviction. |
| `kernel_max_live` | `ABA_KERNEL_MAX_LIVE` | int | 5 | revisit | merge:kernel |  | Per-user SOFT cap on live kernels (evict idle LRU past this). |
| `kernel_threads` | `ABA_KERNEL_THREADS` | str |  | revisit | merge:kernel |  | Override thread count for kernels (else CPU-derived). |
| `mcp_registry_url` | `ABA_MCP_REGISTRY_URL` | str |  | keep | keep |  | MCP registry URL override (else the built-in default). |
| `r_build_jobs` | `ABA_R_BUILD_JOBS` | str |  | move:envspec | merge:r |  | Parallel jobs for R source builds (MAKEFLAGS -j). |
| `r_future_globals_maxsize` | `ABA_R_FUTURE_GLOBALS_MAXSIZE` | str | 8589934592 | move:envspec | merge:r |  | future.globals.maxSize for R in bytes (default 8 GiB). |
| `r_future_plan` | `ABA_R_FUTURE_PLAN` | str | sequential | move:envspec | merge:r |  | future::plan for R (sequential/multicore/…). |
| `r_plot_res` | `ABA_R_PLOT_RES` | int | 120 | move:envspec | merge:r |  | R plot DPI (floored at 40 by the caller). |
| `r_ppm_base` | `ABA_R_PPM_BASE` | str |  | move:envspec | merge:r_ppm |  | Posit Package Manager base URL (else built-in default). |
| `r_ppm_distro` | `ABA_R_PPM_DISTRO` | str |  | move:envspec | merge:r_ppm |  | PPM binary distro tag (else auto-detected). |
| `r_ppm_snapshot` | `ABA_R_PPM_SNAPSHOT` | str | latest | move:envspec | merge:r_ppm |  | PPM snapshot date/tag ('latest' or YYYY-MM-DD). |
| `tool_output_cap_chars` | `ABA_TOOL_OUTPUT_CAP_CHARS` | int | 50000 | keep | keep |  | Per-tool stdout/stderr cap (middle-snip) applied when a kernel result becomes a tool_result block. 0 disables capping. |
| `tool_stream_flush_bytes` | `ABA_TOOL_STREAM_FLUSH_BYTES` | int | 10240 | keep | merge:tool_stream |  | Coalesce kernel output into a tool_chunk SSE event once this many bytes buffer. |
| `tool_stream_flush_interval_s` | `ABA_TOOL_STREAM_FLUSH_INTERVAL_S` | float | 0.5 | keep | merge:tool_stream |  | Max seconds to hold buffered kernel output before flushing a tool_chunk. |

## cluster

*Cluster & job placement (weft absorbs most of this).*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `batch_submitter` | `ABA_BATCH_SUBMITTER` | str | local | retire | keep | branches deploy | Batch backend: 'local' (the local lane — a bare weft task when the compute substrate is up, else the in-process worker), 'slurm', or 'worker' (force the legacy in-process worker). Forwarded into the SIF — it's the placement SELECTOR; unset inside the container → every background job silently runs in-process on the session node. |
| `hpc_config` | `ABA_HPC_CONFIG` | str |  | retire | relocate:hpc.yaml | deploy | Path to hpc.yaml compute-topology override (else $ABA_HOME/hpc.yaml). Forwarded into the SIF alongside the submitter (partition/QOS catalog). |
| `inline_auto_max_cores` | `ABA_INLINE_AUTO_MAX_CORES` | float | 8.0 | retire | merge:inline |  | Max cores an auto-inline job may claim before offloading. |
| `inline_auto_max_mem_gb` | `ABA_INLINE_AUTO_MAX_MEM_GB` | float | 32.0 | retire | merge:inline |  | Max memory (GB) an auto-inline job may claim before offloading. |
| `inline_stall_cpu_sample_s` | `ABA_INLINE_STALL_CPU_SAMPLE_S` | float | 3.0 | retire | merge:inline |  | CPU sampling window (s) for stall detection. |
| `inline_stall_min` | `ABA_INLINE_STALL_MIN` | float | 20.0 | retire | merge:inline |  | Whole-run silence budget (min) before an inline run is deemed stalled. |
| `slurm_mem_frac` | `ABA_SLURM_MEM_FRAC` | float | 0.85 | retire | keep |  | Fraction of node memory a step may use before routing to the background lane. |
| `slurm_walltime_frac` | `ABA_SLURM_WALLTIME_FRAC` | float | 0.8 | retire | keep |  | Fraction of walltime a step may use before routing to the background lane. |

## nextflow

*Nextflow execution wiring (self-contained compute subsystem).*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `nextflow_bin` | `ABA_NEXTFLOW_BIN` | str |  | move:site | merge:nextflow |  | Dir/launcher for a self-installed nextflow prepended to PATH. |
| `nextflow_cachedir` | `ABA_NEXTFLOW_CACHEDIR` | str |  | move:site | merge:nextflow | deploy | Singularity cache dir for nextflow (else site config). |
| `nextflow_config` | `ABA_NEXTFLOW_CONFIG` | str |  | move:site | merge:nextflow | deploy | Extra nextflow -c config file (else site config). |
| `nextflow_execution` | `ABA_NEXTFLOW_EXECUTION` | str |  | retire | merge:nextflow | branches | Nextflow execution mode ('slurm'/'local'; else site config). |
| `nextflow_head_cores` | `ABA_NEXTFLOW_HEAD_CORES` | int |  | retire | merge:nextflow |  | Per-field head Slurm override for nextflow (cores). |
| `nextflow_head_mem_gb` | `ABA_NEXTFLOW_HEAD_MEM_GB` | int |  | retire | merge:nextflow |  | Per-field head Slurm override for nextflow (mem_gb). |
| `nextflow_head_partition` | `ABA_NEXTFLOW_HEAD_PARTITION` | str |  | retire | merge:nextflow |  | Per-field head Slurm override for nextflow (partition). |
| `nextflow_head_qos` | `ABA_NEXTFLOW_HEAD_QOS` | str |  | retire | merge:nextflow |  | Per-field head Slurm override for nextflow (qos). |
| `nextflow_head_walltime_h` | `ABA_NEXTFLOW_HEAD_WALLTIME_H` | int |  | retire | merge:nextflow |  | Per-field head Slurm override for nextflow (walltime_h). |
| `nextflow_home` | `ABA_NEXTFLOW_HOME` | str |  | move:site | merge:nextflow |  | Persistent NXF_HOME (plugins/assets); else per-run scratch. |
| `nextflow_java_home` | `ABA_NEXTFLOW_JAVA_HOME` | str |  | move:site | merge:nextflow |  | JAVA_HOME for the nextflow head (Java ≥17). |
| `nextflow_local_cores` | `ABA_NEXTFLOW_LOCAL_CORES` | int |  | retire | merge:nextflow |  | Per-field local Slurm override for nextflow (cores). |
| `nextflow_local_max_cores` | `ABA_NEXTFLOW_LOCAL_MAX_CORES` | float |  | retire | merge:nextflow |  | Ceiling on cores for local nextflow execution (else 36). |
| `nextflow_local_max_mem_gb` | `ABA_NEXTFLOW_LOCAL_MAX_MEM_GB` | float |  | retire | merge:nextflow |  | Ceiling on memory (GB) for local nextflow execution (else 180). |
| `nextflow_local_mem_gb` | `ABA_NEXTFLOW_LOCAL_MEM_GB` | int |  | retire | merge:nextflow |  | Per-field local Slurm override for nextflow (mem_gb). |
| `nextflow_local_partition` | `ABA_NEXTFLOW_LOCAL_PARTITION` | str |  | retire | merge:nextflow |  | Per-field local Slurm override for nextflow (partition). |
| `nextflow_local_qos` | `ABA_NEXTFLOW_LOCAL_QOS` | str |  | retire | merge:nextflow |  | Per-field local Slurm override for nextflow (qos). |
| `nextflow_local_walltime_h` | `ABA_NEXTFLOW_LOCAL_WALLTIME_H` | int |  | retire | merge:nextflow |  | Per-field local Slurm override for nextflow (walltime_h). |
| `nextflow_module` | `ABA_NEXTFLOW_MODULE` | str |  | move:site | merge:nextflow | deploy | Lmod module providing nextflow (else site config). |
| `nextflow_profiles` | `ABA_NEXTFLOW_PROFILES` | str |  | retire | merge:nextflow | deploy | Comma-separated nextflow profiles (else site config). |
| `nextflow_workdir` | `ABA_NEXTFLOW_WORKDIR` | str |  | move:site | merge:nextflow |  | Nextflow work dir root (else site config). |

## bundle

*Bundle scope resolution + site config.*

| setting | env | type | default | weft_fate | reduction | flags | doc |
|---|---|---|---|---|---|---|---|
| `composed_bundle_path` | `ABA_COMPOSED_BUNDLE_PATH` | str |  | keep | keep |  | Precomposed effective-bundle path (future marker). |
| `group` | `ABA_GROUP` | str |  | keep | keep | deploy | Group/lab id for group-scoped bundle + credentials. |
| `institution_bundle` | `ABA_INSTITUTION_BUNDLE` | str |  | keep | keep |  | Override path for the institution bundle scope. |
| `lab_bundle` | `ABA_LAB_BUNDLE` | str |  | keep | keep |  | Override path for the lab bundle scope. |
| `scratch_dir` | `ABA_SCRATCH` | str |  | keep | keep |  | Optional user scratch dir (else site config). |
| `site_config` | `ABA_SITE_CONFIG` | str |  | keep | keep | deploy | Path to the deployment site.yaml (scopes, credentials, paths). |
| `state_dir` | `ABA_STATE_DIR` | str |  | keep | keep |  | User state dir (else $ABA_HOME/state or site config). |
| `system_bundle` | `ABA_SYSTEM_BUNDLE` | str |  | keep | keep |  | Override path for the system bundle scope. |
| `user_bundle` | `ABA_USER_BUNDLE` | str |  | keep | keep |  | Override path for the user bundle scope. |

