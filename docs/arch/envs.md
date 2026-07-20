# Environments & provisioning

How ABA provides the software a run executes in — Python and R — and how it adds
packages on demand without corrupting shared state.

> The maintained reference for how environments work.

## Aims & principles

The scientific stack is **ABI-fragile**: every compiled package (numpy, scipy, scanpy,
numba, torch…) is built against one numpy ABI, and a single unpinned install can move numpy
and silently break `import` for unrelated code. So the model is **integrity-safe by
construction** — and the way that safety is achieved is that ABA *describes* environments
while **weft realizes them**, content-addressed and locked:

- **ABA describes; weft realizes — through one doorway.** An environment is a **spec** ABA
  hands to the compute substrate; weft solves it to a locked, content-addressed **EnvID** and
  materializes the on-disk prefix. Every environment operation goes through
  `core/compute/ports.py` (`env_ensure` / `env_evict` / `env_status` / `session_*`), and the
  *only* `import weft` in the tree is `core/compute/adapter.py:105`. So the realization
  strategy (a local directory, a squashfs image mounted read-only on a cluster node, a remote
  site) can change without touching a caller. See [`compute-sites.md`](compute-sites.md) for
  the site/adapter surface this shares.
- **Never mutate a *shared* environment in place.** A base is **immutable and shared**
  (content-addressed — nobody can shadow-break it); a project's live installs land in that
  project's **own** weft session; an isolated env is **frozen** and grows only by solving a
  *new* EnvID. One project can't corrupt another or the base.
- **The lock is the durable truth; the prefix is a rebuildable cache.** An EnvID names a
  solved lock. The materialized prefix is disposable — evicted or garbage-collected, it
  rebuilds from the lock on next use. This is what makes reclaiming disk safe and what makes
  the *same* env reproducible on a second machine.
- **Verify by loading, and fail loud.** A capability is confirmed by actually importing it
  (not `find_spec`); a broken or missing env is surfaced (startup self-check,
  `gpu_capability_ok`), never left as a latent landmine.

## The model

Environments are realized by weft and identified by **EnvID** (weft's content-addressed
identity). ABA keeps per-project `name → EnvID` handles in `PROJECTS_DIR/<pid>/weft_envs.json`
(three namespaces: `envs`, `active`, `default`). Three tiers, all Python **and** R:

```
 bundle envs/ facet (role: base) ─► env_packs.pack_spec ─► env_ensure ─► EnvID
                                                                            │
   base pack (base_env) ───clone──► project default (project_env)          │
   immutable, shared,               a weft SESSION, per-project:           │
   content-addressed                · kernels + local one-shot runs        │
   (adopt a published image,        · ensure_capability → session_install  │
    else solve locally)               (installs land LIVE, in place)       │
                                     · session_snapshot → a frozen EnvID    │
                                       for background jobs / exports        │
                                                                            │
   named / isolated envs (named_envs) ── extend ──► a NEW EnvID ───────────┘
   frozen; extend never installs into a frozen env
   the ONE weft doorway: core/compute/adapter.py → core/compute/ports.py
```

- **Base pack** (`core/compute/base_env.py`) — the shared scientific foundation, declared as
  a bundle `envs/` facet with `role: base`, a per-language `languages:` list, and a verbatim
  weft `spec:` block (plus optional `import_names`). `require(language)` resolves it — there is
  **no served-base/micromamba fallback**: a deployment that runs a language **must** declare a
  `role: base` env pack (a missing one is a loud, structured misconfiguration). `env_id()`
  **adopts** an EnvID from a published catalog when one exists (the managed-cluster path —
  `seeding.publish_base_packs` / `adopt_env_id`), else solves the spec locally.
- **Project default env — a weft session over the base pack** (`core/compute/project_env.py`).
  The per-project default is a **session** off the base. What a session *runs from* is the
  substrate's fact, consumed as weft's **runtime block** `{source: session|base, env_id,
  prefix, activation, ns_wrap, direct_exec}` (`session_runtime`, observation-only): the clone
  may be **lazy** — a zero-delta session runs from its base realization in place
  (`source: "base"`, identity = the base EnvID) until the first `session_install`
  **materializes** its own clone (the *flip moment* — the install result carries the fresh
  block, and a mutated session is honestly identity-less scratch until snapshot). ABA never
  probes prefix existence for liveness (`ensure()` asks `session_runtime`; only a truly
  pruned session rebuilds + replays recorded additions), and one-shot lanes compose commands
  through `project_env.argv_for_runtime(...)` — direct `prefix/bin/*` exec only when
  `direct_exec`, else through the activation line (inside `unshare -rm` when `ns_wrap`;
  squashfs bases are mount-scoped and have no path outside their activation).
  `interpreter()`/`prefix()` refuse typed (`session.no_direct_exec`) rather than hand out a
  dangling path. Against a pre-runtime (eager-cloning) weft, an activation-shaped shim
  (`_shim_runtime`) synthesizes the block — deleted once every deployment's weft exposes
  `session_runtime`. `ensure_capability` installs land **live** in the session
  (`session_install`). Because a live session is mutable, background jobs and exports don't
  run it directly — they run a `session_snapshot` **EnvID** (frozen, dirty-cached; weft
  returns the base EnvID for a zero-delta session rather than minting a duplicate). The
  registry is the `default` key of `weft_envs.json`. Guard: `tests/test_lazy_session_lane.py`
  runs the lane under BOTH substrate personalities (eager and lazy) × both topologies
  (direct-exec and activation-only).
- **Named / isolated envs** (`core/compute/named_envs.py`) — the escape hatch for a hard
  conflict or a deliberately-pinned stack: a fully separate, **frozen** EnvID. `create(...)`
  solves a fresh one; `extend(name, packages)` adds packages as an `extends_env` layer over the
  current EnvID → a **new** EnvID the handle then points at — it **never installs into a frozen
  env**. Each named env carries its own persistent kernel and its own reproducible lock.

CLI tools that are *executables*, not importable libraries (samtools, STAR, nextflow), are a
content-addressed **tool env** of their own (`named_envs.ensure_tool_env`), exposed to runs via
PATH — the weft successor to the old micromamba tools env.

## Provisioning — adding a capability on demand

The agent calls `ensure_capability(name)` (`content/bio/tools/discovery.py:884`); it resolves
the capability record (the catalog entity + bundle composition are owned by
[`bundle-and-content.md`](bundle-and-content.md)) and provisions by target lane:

- **Default lane → the project session.** A pip/library capability installs **live** into the
  project's default weft session via `project_env.install(...)` (`session_install`). Nothing is
  shadow-stacked on a frozen base: the session is a single coherent weft-solved environment, so an
  install is re-solved against the whole set.
- **Named lane → a frozen env.** A request scoped to a named env routes to
  `named_envs.create` / `extend`, which solve a new EnvID rather than mutating one.

`core/exec/materialize.py` is now only the **subprocess run harness**: `MaterializingExecutor`
supplies the ABA-runtime venv that *launches* a one-shot script (`_base_env`), while the
science interpreter comes from the weft env. Its old `materialize()` provisioning dispatch
(pip-into-overlay, conda, container) **raises `NotImplementedError`** — conda and tool envs are
weft's now.

The local run lane selects its interpreter accordingly (`core/exec/run.py`):
`env=<name>` → `named_envs.interpreter()`; a pre-resolved job-spec snapshot → that EnvID's
python; else the default → `base_env.require("python")` + the session **runtime block**
(`project_env.runtime()` → `argv_for_runtime`, topology-blind). The best-effort env
fingerprint is skipped (never faked) when no direct interpreter path exists.

## Platform membership (multi-site envs)

An env lock's **platform set is part of its identity**: adding a platform yields a **new**
EnvID solved for all members. ABA's specs lock for the **controller's** platform by default;
when a run targets a site with a different OS/arch, weft surfaces a typed
`env.platform_mismatch` at realize time and ABA re-locks **lazily, once**, then transparently
retries:

- **Named env** — `named_envs.ensure_platform(project_id, name, platform_str)` re-solves from
  the row's **persisted `base_spec`** (its `python_version` pin included) and replays each
  `extend()` layer as an `extends_env` link, adding the target platform (`env_ensure(update=True)`).
  Replaying *as built* is load-bearing: reconstructing from a flattened package list once
  silently re-locked a pinned-3.10 env to the default 3.12 and dropped the layering.
- **Base / project default** — `base_env.ensure_platform(language, platform_str)` re-solves the
  verbatim pack spec for the added platform → a new EnvID (a live session's dirty extras don't
  travel — the same trade the snapshot lane makes).

Callers wire the retry-once in three places, at parity: the one-shot detached submit and its
poll-side resubmit (`core/jobs/weft_submitter.py`), and the interactive remote kernel lane
(`core/exec/kernels/weft.py`). Solve cost and platform-availability failures land on the remote
attempt, never on local work — a package with no build for the site's platform fails **that**
submission with a named cause. See [`jobs-and-hpc.md`](jobs-and-hpc.md) and
[`compute-sites.md`](compute-sites.md).

**`env='system'`.** An explicit lever for stdlib-only steps (downloads/transfers, listings,
checksums): `env='system'` (or `'none'`) skips pack realization entirely and runs the machine's
own `python3` off PATH — right for a step a 1.5 GB scientific env would serve nothing. Env choice
is orthogonal to execution mode: a synchronous `site=` step gets the same **persistent session**
as any env, just attached bare (`WeftKernelSession` with neither `env_id` nor `session_id`;
weft's `kernel_start` default), so state carries between calls; a detached job runs one-shot on
the node interpreter and is graded `env_grade: node-system` on its exec record
([`provenance.md`](provenance.md)). Nothing is installable into a bare kernel — `ensure_capability`
targets the project session, not the node's interpreter.

## Integrity, verification & disk reclaim

- **Real-import verification** (`verify_python_imports`, `core/exec/verify.py:22`) — a
  capability is confirmed by importing it, not by `find_spec`; a present-but-unloadable
  (ABI-mismatched) package is caught, not reported ready.
- **Content-addressing *is* the ABI guard.** There is no per-install version-pinning step because
  there is no incremental mutation of a shared base to guard: a named env is a single frozen solve,
  a session install re-solves the project's own env, and the base is immutable and shared by EnvID.
- **Read-only diagnostics** (`core/exec/env_integrity.py`) — `env_overview` / `env_layers` /
  `python_package_status` probe the **weft session** (the (i)-drawer Env tab);
  `ensure_sys_executable` recovers an empty `sys.executable` at startup.
- **Safe disk reclaim via eviction** (`core/modules/reconciler.py`). Because the lock is the
  durable truth, reclaiming a pack-backed module's bytes is `env_evict(env_id, site)` — the env
  rebuilds from its lock on next use (`ensure_realized` / `_run_realize_task` with `force=True`
  bypass weft's memo so an evicted prefix actually rebuilds). If weft refuses because a
  session/kernel/job holds the env, ABA stops **only kernel-less session holders** and retries
  once; live kernels and jobs are surfaced honestly, never killed. (Pre-weft, "reclaim disk"
  rmtree'd a dead `$TOOLS_ENV` path and silently did nothing — the bug this closes.)
- **Background-job env parity** ([`jobs-and-hpc.md`](jobs-and-hpc.md)) — a job runs the same
  base/session env as an interactive run (as a `session_snapshot` EnvID, or a named env's EnvID),
  realized on the node by weft; `slurm_entry` reads the activated env off `$CONDA_PREFIX`, so a
  cluster `module load` can't shadow the interpreter.

## GPU / accelerator (target hardware)

A step's *hardware-variant* need (a CUDA build of torch vs the CPU build) is a distinct axis
from its *library* needs, and lives at the **base** tier, not the library tier:

- **Hardware variant → the base, chosen at install** (deployment-conditional). `torch` arrives
  transitively via `scvi-tools`, and conda-forge's default is the **CPU-only** build; a GPU
  deployment builds a **CUDA** base instead. The choice is one toggle in `$ABA_HOME/config.env`
  — `ABA_ACCELERATOR=cpu|cuda` (+ optional `ABA_CUDA_VERSION`) — written by the linux/cluster
  installer (which auto-detects a `gpu` Slurm partition) and applied by
  `install/core/inject-accelerator.sh`, which injects a CUDA `torch` pin into the base spec. A
  CUDA torch is a **superset**: it uses a GPU when present and falls back to CPU on the login
  node / CPU jobs, so one base serves both. The base builds on the GPU-less login node — there
  is no build-time GPU — so the installer exports `CONDA_OVERRIDE_CUDA` to let the solver accept
  the CUDA build (and select the CUDA major); `11.8` is the widest-compat default (driver
  ≥450.80, GPUs P100…H100). **Non-torch GPU frameworks** (jax[cuda], RAPIDS) are the library
  axis — a session install or an isolated env, not the base.
- **Certainty across nodes = discover-once + verify-at-use** (ABA runs on a CPU login node; a
  job runs on a GPU node ABA can't observe):
  - **`gpu_usable`** — a node-independent readiness hint in the agent's per-turn cue
    (`core/exec/compute_env.py`), true when a GPU is present *and* the base torch is a CUDA
    build (`torch_cuda_build`, `verify.py:96` — a property of the build, not of runtime GPU
    visibility). If a GPU exists but the base is CPU-only, the cue **warns** so the agent runs
    on CPU / tells the user instead of submitting a job that silently falls back.
  - **Verify-at-use** — a GPU-requested job is preflighted on the compute node via
    `gpu_capability_ok()` (`verify.py:72`, called in `core/jobs/slurm_entry.py`); no usable GPU
    → it **fails fast** rather than training on CPU on an idle allocated GPU (the scVI-on-CPU
    incident: right placement, CPU base).
  - **`aba doctor` / startup self-check** — a deployment declaring `ABA_ACCELERATOR=cuda` with a
    CPU-only base is flagged, with the fix named (set the toggle + rebuild the env).

**Shared-FS reachability under Slurm** (`env_integrity.check_envs_dir_shared` /
`check_base_dir_shared`). A background job on a compute node must be able to *reach* the env the
controller provisioned, and *how* it reaches it depends on the delivery mode: with **bare
offload** (a native install or a slim SIF) the node runs the interpreter directly, so the env
area **and** base must sit on **shared FS**, classified empirically by mount fstype
(`/proc/self/mountinfo`), not path prefix; with **wrapped offload** (`ABA_JOB_WRAP=sif`, a fat or
weft SIF) the job re-enters the image via `apptainer exec`, so an in-image base is correct, not a
defect. Under the default **weft SIF profile** the image bakes only the slim controller runtime —
the science envs are **weft images adopted read-only on the node** (via the site's `ro_roots`,
the deployment's published env tree) — so an offloaded job reaches its interpreter either way.
Install-time is a hard gate (`aba doctor` + a definitive `sbatch` probe on a native install); a
loud-but-boot **runtime self-check** surfaces the rest on `/api/health` (`degraded` +
`warnings[]`) and `/api/admin/selfcheck` — the guard that still fires under a SIF/OOD deploy where
the install-time probe can't run.

## Key implementation references

| Where | What |
|---|---|
| `core/compute/adapter.py` · `ports.py` | the **one** weft doorway (`from weft.api import Weft`, `:105`) + the abstract compute port (`env_ensure`/`env_evict`/`env_status`/`session_*`/`task_*`) |
| `core/compute/env_packs.py` | bundle `envs/` facet → weft `EnvSpec` → `env_ensure` → EnvID; `pack_spec`, `import_names` maps |
| `core/compute/base_env.py` | the shared base pack: `require(language)` (no served-base fallback), `env_id()` (adopt-or-solve), `ensure_platform`, `interpreter`/`prefix` |
| `core/compute/project_env.py` | the per-project **default env as a weft session**: `ensure` (runtime-block liveness, rebuild+replay), `runtime`/`argv_for_runtime`/`exec_argv` (topology-blind one-shot argv), `install` (live `session_install`, flip-aware), `snapshot` (frozen EnvID for jobs/exports), `stop_all_sessions`, `reset` |
| `core/compute/named_envs.py` | named/isolated **frozen** EnvIDs: `create`/`extend` (extend→new EnvID), `ensure_ready`/`ensure_realized`, `ensure_platform`, `ensure_tool_env` (CLI tools) |
| `core/compute/seeding.py` | managed-cluster catalog: `publish_base_packs` / `adopt_env_id` (published `image.sqfs` keyed by EnvID) |
| `core/exec/verify.py` | the honest runtime probes: `verify_python_imports`, `gpu_capability_ok`, `torch_cuda_build` |
| `core/exec/env_integrity.py` | read-only diagnostics (`env_overview`/`env_layers`/`python_package_status`), `ensure_sys_executable`, the Slurm shared-FS self-checks (`check_envs_dir_shared`/`check_base_dir_shared`) |
| `core/modules/reconciler.py` · `manager.py` | disk reclaim via `env_evict(env_id, site)` (rebuild-from-lock), stop-kernel-less-holders-and-retry |
| `core/exec/run.py` (`:44-72`) · `core/exec/kernels/weft.py` | run-lane interpreter selection (named / snapshot / base+session); the remote kernel platform re-lock |
| `content/bio/tools/discovery.py` | agent surface: `ensure_capability` → `project_env.install` / `named_envs`, `propose_capability`, `search_bioconda`/`search_pypi` |
| `install/core/inject-accelerator.sh` · `install/linux/setup.sh` | deployment-conditional base torch: `ABA_ACCELERATOR` → CPU vs CUDA pin (+ `CONDA_OVERRIDE_CUDA`), auto-detected |

## Known gaps

- **Accelerator selection is an install-time base fact, not yet a weft site fact.** The
  `ABA_ACCELERATOR` toggle (`weft_fate="move:site"`) drives the installer's base build today; its
  intended home is per-**site** weft config, so that one controller could dispatch CUDA work to a
  GPU site and CPU work elsewhere from a single base description. That migration is not built —
  the CPU/CUDA choice is still a per-deployment base variant.
- **Install-time GPU verify & build-on-target.** Per-job `gpu_capability_ok` verifies at *run*
  time, but ABA does not yet confirm at *install* that the built CUDA runtime initializes on each
  GPU partition (driver new enough), nor build node-arch-specific artifacts (source-only wheels,
  `-march=native`, CUDA extensions) on the target partition. The login-node build is the wrong
  hardware for those; a per-partition build-into-a-job + wheel cache is designed, unbuilt.
- **Stale in-code docstrings.** `content/bio/tools/discovery.py`'s `ensure_capability` docstring
  still says "wipeable overlay" and `core/exec/materialize.py`'s module header still describes the
  `ENVS_DIR/pylib` overlay — both pre-weft text; the code routes to `session_install` / raises.
  Trust the behavior described above, not those headers.
- **Direct-path residue outside the default lane.** The default lane is topology-blind
  (`argv_for_runtime`), but `named_envs.interpreter()` still hands out a bare prefix path
  (mount-scoped named-env realizations would need the same activation treatment —
  `named_envs.run_in` already routes through weft when no ready prefix exists), and a few
  presentation/probe surfaces are direct-exec-only by construction (`env_layers` site-dir
  scans, `_session_site_dirs`, the viewer launchers' interpreter resolution, run-lane env
  fingerprints). All degrade honestly (typed refusal / omitted layer / skipped fingerprint)
  rather than lie, but on an activation-only topology they under-report; migrating them to
  argv/runtime consumption is backlog.
