# Background jobs & HPC

How ABA runs work that outlives a single agent turn — a long `run_python`/`run_r`/
`run_nextflow` — as a **background job**, on this node or offloaded to a Slurm cluster,
and how the finished job wakes the agent back up.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

Interactive `run_python` is owned by [`compute-execution.md`](compute-execution.md) (the
kernel pool, run-NOW, artifact harvest). This doc owns the **LATER** path: a step too
long or too heavy for the live kernel becomes a durable **job**. The routing decision
(interactive vs background, and the `estimated_runtime_min` that drives it) lives in the
run tool and is summarized there; a job's life begins once `submit_*_job` is called.

Four invariants shape everything here:

- **Placement is pluggable behind one protocol.** *Where* a job runs — this process, a
  Slurm allocation, an in-place subprocess — is a `BatchSubmitter` chosen by
  `get_submitter`, never an `if slurm:` sprinkled through the runner. The job **row** is
  created identically for every submitter; the submitter only decides how it *runs* and
  how its status/cancel/monitor resolve. **Failure it prevents:** a new backend (arq, a
  different scheduler) forcing edits across the whole lifecycle instead of one class.
- **A finished job re-enters the reasoning loop.** The agent turn that submitted the job
  already ended, so its planned downstream steps (UMAP after training, QC after a
  pipeline) would never run. **Every** terminal transition fires a **continuation**: a
  synthetic turn on the originating thread that resumes the plan. **Failure it prevents:**
  a completed job that silently produces artifacts nobody acts on — a dead-ended plan.
- **Certainty across nodes = discover-once + verify-at-use.** ABA runs on a login/CPU node
  that **cannot observe** the compute node a job lands on. So the submit side *discovers*
  the landscape (`sinfo`/`sacctmgr`) and *asserts* a request; the compute side **verifies
  at the point of use** (GPU present, numpy importable) and fails loud if the assertion was
  wrong. **Failure it prevents:** a GPU job silently training on CPU on an idle allocated
  GPU (the scVI-on-CPU incident).
- **Job env hygiene is non-negotiable.** A background job runs the **same** base+overlay
  interpreter as an interactive run (owned by [`envs.md`](envs.md)); a `module load` in the
  job script must not shadow it. `slurm_entry`'s wrapper clears `PYTHONHOME` and pins
  `PYTHONPATH` **after** the module load. **Failure it prevents:** an ancient cluster-module
  Python breaking `import numpy` deep in the user's code (the prj_6d986f40 incident).

## The model

A **job** is a row in the per-project `jobs` table (`core/graph/jobs.py`) — a `kind`
(`run_python`/`run_r`/`run_nextflow`/`import_run`), a `status`
(`queued→running→done|failed|cancelled`), and a `params` blob that carries everything the
worker needs decoupled from live process state: the `code`, the captured `project_id` /
`thread_id` / `run_id`, the agent's `estimate`, the resolved `submission` target, and (once
dispatched) the `slurm_id` / `run_dir` / `resources`. Capturing project+thread+run **at
submit time** is what lets the job attach its outputs to the right Run even after the active
project changes.

A **`BatchSubmitter`** (`core/jobs/submitter.py:24`) is the pluggable placement — a Protocol
of `submit`/`cancel`/`poll`/`info`. Two implementations exist:
`LocalSubmitter` (`runner.py:56`) runs the job in **this** process' async worker;
`SlurmSubmitter` (`slurm_submitter.py:36`) `sbatch`es it and resolves completion off the
shared filesystem. `get_submitter()` picks the deployment default from
`ABA_BATCH_SUBMITTER=local|slurm` (`submitter.py:47`) — a `config.env` toggle exactly like
`ABA_ACCELERATOR` (config topology owned by [`deployment-and-access.md`](deployment-and-access.md)).

```
run_python/run_r(background) ─ submit_*_job ─► create_job(row, status=queued)
                                                    │  get_submitter_for(submission)
                       ┌────────────────────────────┴──────────────────────────────┐
             LocalSubmitter.submit                                       SlurmSubmitter.submit
             enqueue → _worker → _run_one                                sbatch job.sh → slurm_entry
                       │  run_python_code, in-process                    │  writes result.json, then `done`
                       │                                       _slurm_poll_loop watches the `done` sentinel
                       └───────────────► _finalize_job(result) ◄─────────┘  poll() → result-shaped dict
                                             │  exec record → on_job_complete (harvest) → status=done|failed
                                             ▼
                                   enqueue_continuation → _fire → reasoning_port.run_continuation
                                        (→ guide's registered handler; a fresh turn resumes the plan)
```

The seam is that **both** placements converge on one `_finalize_job` (`runner.py:649`): map
result→status, write the run log, stamp the provenance exec record, register artifacts via
the `on_job_complete` hook, then fire the continuation. Local and Slurm differ only in how
the result *arrives*.

## The worker, the poll loop, and restart survival

**Local.** A single async worker (`_worker`, `runner.py:827`) pulls `(job_id, project_id)`
tuples off an in-memory `asyncio.Queue` and runs them **one at a time** through `_run_one`
(`runner.py:722`) — the same execution core as synchronous `run_python`, so the job sees the
project overlay, tools env, and killpg cancellation. This is deliberately single-process and
sequential — fine for the single-user prototype; arq+Redis is the multiuser successor
A per-job `CancelToken` keyed by `job_id` lets `cancel_job` killpg the
whole process group.

**Slurm.** `SlurmSubmitter.submit` (`slurm_submitter.py:44`) writes a `job_spec.json` and a
`job.sh` that runs `python -m core.jobs.slurm_entry <spec>` then `echo $? > done`, sizes the
allocation via `resolve_resources`, and `sbatch --parsable`es it. There are **no
callbacks/webhooks**: completion is signaled by the **`done` sentinel** on the shared FS.
A dedicated `_slurm_poll_loop` (`runner.py:1161`, started only when the submitter is `slurm`)
scans active Slurm rows every 8s and calls `poll()`; a non-`None` result routes through the
same `_finalize_job`.

`poll()` (`slurm_submitter.py:175`) encodes the cross-node truth carefully: the **sentinel is
authoritative** — `done`'s exit code + `result.json`. With no sentinel yet, the job is alive
iff **`squeue`** lists it (the *live* state); `sacct` is consulted **only** after it leaves
`squeue`, because `sacct` can return a *stale historical* record for a reused job id (a dev
cluster whose counter reset) and wrongly fail a job about to run. A clean-exit sentinel whose
`result.json` isn't readable yet is treated as **NFS visibility lag** and re-polled (bounded
by mtime), never as an empty result — returning the empty fallback once silently dropped a
whole Seurat run's artifacts.

**Restart survival.** Because ABA is a single process, a restart must reconcile state.
`reconcile_jobs` (`runner.py:862`), run once at startup **before** the worker drains the
queue, sweeps every project DB: local `running` rows are **zombies** (their worker died with
the process) → marked `failed`; `queued` rows are re-enqueued in global `created_at` FIFO.
The pivot is `_is_slurm_params`: a **Slurm** job keeps running on the cluster across the
restart, so reconcile must **not** reap or re-enqueue it — the poll loop re-adopts it via the
sentinel. **Sync weft rows** (owned in-tool by `_run_remote_sync`'s wait loop, which the
background poll loop deliberately skips) lose their only finalizer with the process:
reconcile **adopts** substrate-accepted ones into the poll loop (`sync` flipped off,
`sync_orphaned` stamped) and reaps ones that never reached the substrate. (Two watchdogs run
alongside: `_slurm_poll_loop` for external jobs and `_inline_watchdog_loop` (`runner.py:1229`)
for wedged inline pipeline heads.)

**Finalize ownership — one writer, one verdict.** Three invariants close the
false-"infra failure" class (a task the substrate finished cleanly being recorded failed —
misc/bug1.md): (1) **the jobs-plane lease** (`_acquire_jobs_lease`): an exclusive flock on
`<runtime>/jobs.lease` lets only the FIRST instance run worker/reconcile/poll loops — a
second instance (e.g. a stale installed backend the tray briefly boots) serves its API but
logs a loud refusal instead of finalizing rows it doesn't understand. (2) **transport truth
at poll**: `WeftSubmitter.poll()` re-reads the persisted row and, absent the `detached`
stamp, derives the branch from the site's declared contract — no caller's stale dict can
route a detached task into the controller-local `result.json` check; a terminal-DONE task
whose result isn't readable yet gets bounded grace retries, then an honest verdict that says
the entry ran. (3) **single-verdict finalize**: `_finalize_job` ignores (with a WARNING) any
row already terminal, and a success verdict clears `error` — the contradictory
`done`+stale-error row cannot be minted. Every failure verdict, branch decision, and
second-finalize attempt is logged.

## Placement: the submitter seam

`get_submitter_for(target)` (`submitter.py:62`) is the per-job override that makes placement
finer than a deployment default: `'inline'` → `LocalSubmitter` (run in ABA's own
process/allocation, no `sbatch`), `'slurm'` → `SlurmSubmitter`, anything else → the default.
`resolve_submission_target` (`runner.py:324`) decides, at submit, whether a *local-executable*
job (`execution` in `local`/`auto`) actually fits ABA's free allocation — routing it to Slurm
when ABA is on a login node, when the heaviest task exceeds free cores/mem, or (for `auto`)
when the job is substantial enough to be worth fanning out. This is why cancel routes through
`_submitter_for_job` (`runner.py:469`) keyed on `params.submission`, **not** the deployment
default: on a Slurm deployment a small job may still have run inline, and `scancel`-ing a job
with no Slurm id would orphan the live inline process. The nesting is intentional: on a
cluster ABA itself runs as a Slurm job and `sbatch`es **further** jobs from its compute node.

`resolve_resources` (`hpc_config.py:118`) maps the agent's **estimate** (`runtime_min` +
optional `cores`/`mem_gb`/`gpu`) onto a concrete request: pick the first partition that fits
the gpu-match and ceilings, clamp to it, cap walltime to the QOS `MaxWall`. A GPU need becomes
`--gres=gpu:1`; a heavy pipeline head is sized from the site's Nextflow config, not the task
estimate.

## Continuation — the job re-enters the loop

When `_finalize_job` reaches a terminal state it calls `enqueue_continuation`
(`continuation.py:41`). If the originating thread has an actively-streaming turn, the
continuation **defers** — a background task polls until the thread goes idle (bounded by
`DEFER_TIMEOUT_S`) — otherwise it fires immediately. `_fire` (`continuation.py:449`) binds the
project, then re-enters the agent through the **reasoning-plane port**
(`reasoning_port.run_continuation`, which calls guide's registered handler) on a fresh
`run_id`: a new durable turn, exactly as if the user had typed. Core does **not** import
guide — the port is the up-edge (see Known gaps + `check_seam.sh` rule 4). The trigger is a **synthetic user message**
(`_continuation_message_text`, `continuation.py:292`) prefixed `[continuation: …]` (the
frontend badges it) whose text is tailored per outcome — success-with-artifacts ("continue
the plan"), done-but-**zero**-artifacts (silent-failure shape: "inspect run.log, don't claim
success"), failed (the error + a hint to reload inputs from disk, since a job runs in a fresh
process with none of the kernel's objects), cancelled ("acknowledge and STOP"), and a
Nextflow/import branch that surfaces the pipeline's own QC. **Every** outcome notifies —
originally cancelled was skipped, which left the tool line spinning and the agent un-notified.
The mechanics of durable turns and resume are owned by [`agent-loop.md`](agent-loop.md); this
module is the compute-plane producer that re-enters it.

Provenance is not special-cased for jobs: `_write_exec_record_for_job` (`runner.py:576`)
stamps the **same** exec record an interactive run does (code + env fingerprint + produced
outputs), injecting `exec_id` **before** registration so artifacts attach and stay
reproducible — see [`provenance.md`](provenance.md).

## Discovery, hygiene, and verify-at-use

**Discover-once (submit side).** `hpc_config` (`hpc_config.py:61`) resolves the deployment's
compute landscape with precedence `$ABA_HPC_CONFIG`/`$ABA_HOME/hpc.yaml` → the bundle's
`hpc:` settings → **live auto-detection**. When nothing pins them, partitions and QOS+account
are read live from the deployment's slurm-kind **weft site** through the SitePort:
`sites_describe` for partitions (`hpc_config._live_partitions`) and `site_associations` for
QOS+account (`hpc_config._live_qos_account`, ranked most-permissive first, carrying the primary
QOS's `MaxWall` as a walltime cap). So an unconfigured cluster still routes GPU/large jobs to
real partitions and submits the right `--qos`/`--account` with no `hpc.yaml` at all — the file
is a pure optional override. The same weft SitePort (`sites_describe` + `site_load` +
`site_associations`) is the read model behind the agent's `describe_compute` surface —
`compute_env._cluster_landscape` (partitions, node sizes, queue depth, wait hints). *(Bucket 2:
the legacy `slurm_live.py` local-`sinfo`/`squeue`/`sacctmgr` read model was retired; a slurm
site's live queries now run through weft. One behavior note: weft's capability record carries
no default-partition marker, so `_live_partitions` no longer orders the default partition
first.)*

**Hygiene + verify-at-use (compute side).** The generated `job.sh` runs `module load` for the
project's resolved cluster modules, then — critically — `unset PYTHONHOME` and
`export PYTHONPATH=<backend>` (`slurm_submitter.py:107`) so the conda env's Python uses its
own stdlib+site-packages while a module's PATH/`LD_LIBRARY_PATH` for *tools* survive. Inside,
`slurm_entry.main` (`slurm_entry.py:16`) runs the code through the same `run_python_code`/
`run_r_code` core (artifacts harvest to the shared store identically), guarded by two
preflights: a **numpy canary** (`verify_python_imports(["numpy"])`) that fails loud if a
module still shadowed the env, and a **GPU verify-at-use** — when the agent requested a GPU,
`gpu_capability_ok()` must confirm a usable CUDA torch on *this* node or the job aborts
actionably rather than burning the allocation on CPU. Both helpers, and the CPU-vs-CUDA base
they check against, are owned by [`envs.md`](envs.md); this doc owns their use *at the job
boundary*.

## Key implementation references

| Where | What |
|---|---|
| `core/jobs/submitter.py` | `BatchSubmitter` Protocol; `get_submitter` (deployment default via `ABA_BATCH_SUBMITTER`); `get_submitter_for` (per-job inline/slurm override) |
| `core/jobs/runner.py` | `LocalSubmitter`; `submit_python_job`/`submit_r_job`/`submit_nextflow_job`; `_run_one`/`_worker`; the shared `_finalize_job`; `reconcile_jobs` + `_reap_orphan_processes`; `_slurm_poll_loop`; `_inline_watchdog_loop`; `resolve_submission_target`; `cancel_job` |
| `core/jobs/slurm_submitter.py` | `sbatch` submit (job.sh, PYTHONHOME/PYTHONPATH hygiene, resource flags); sentinel `poll()` (squeue-live vs sacct-historical, NFS-lag grace); `info()` for the monitor |
| `core/jobs/slurm_entry.py` | compute-node entrypoint; same exec core; numpy canary + GPU verify-at-use preflight |
| `core/jobs/hpc_config.py` | `hpc_config` (config → bundle → live weft SitePort); `_live_partitions`/`_live_qos_account` (weft `sites_describe`/`site_associations`); `resolve_resources` (estimate → partition/qos/account/walltime) |
| `core/exec/compute_env.py` | `_cluster_landscape` (weft `sites_describe`/`site_load`/`site_associations`) — the `describe_compute`/`context_line` read model (partitions, node sizes, queue depth, wait hints) |
| `core/jobs/continuation.py` | `enqueue_continuation` (defer/fire); `_fire` → `reasoning_port.run_continuation` (→ guide's registered handler); per-outcome `_continuation_message_text` |
| `core/reasoning_port.py` | the Compute→Reasoning up-edge port: `register_continuation` (guide, at import) / `run_continuation` (continuation.py); mandatory, raises if unregistered |
| `core/graph/jobs.py` | the `jobs` table: `create_job`/`update_job`/`get_job` |
| `content/bio/tools/run_exec.py` · `guide.py` | callers of `submit_*_job` (background routing; the estimate) |

## The weft local lane

The **local background lane defaults to a bare weft task** when the compute substrate is up
(`core/jobs/weft_submitter.py`): `submit_*_job` with target `inline` (and the deployment
default when `ABA_BATCH_SUBMITTER=local`) hands the job to `WeftSubmitter`, whose task runs
the SAME node entry as the Slurm lane (`python -m core.jobs.slurm_entry job_spec.json`) — so
`result.json`, harvest, and exec records stay identical. `runner._weft_poll_loop` (always
started; idles while the substrate is offline) watches for terminal states and routes through
the SHARED `_finalize_job` → continuation. The exec record additionally carries a
**weft-sourced `compute` block** — task identity + `placement {site, node, allocation_id,
node_truth, ran_at}` + wall/rss — placement is circumstance, never identity. Substrate
offline → loud fallback to the legacy in-process worker; `ABA_BATCH_SUBMITTER=worker` forces
it. Reconcile treats weft rows like Slurm rows (external — never reaped/re-enqueued); cancel
routes by hard evidence (`params.weft_id` → `task_cancel`). Note the lane also FIXED a
long-standing default: `execution=None` on a non-slurm deployment used to resolve to
`sbatch` (dead on personal installs); it now resolves to the local lane.

## The detached transport

`site=` on `submit_python_job`/`submit_r_job` (and the `run_python`/`run_r`
tools) targets ANY declared weft site — the orthogonal *which-machine* axis;
explicit `site` wins over `execution`. Placement is independent of *duration*:
`site=` alone runs SYNCHRONOUSLY (`content/bio/tools/run_exec._run_remote_sync`
submits, waits in-tool, returns a normal result — like a local call executed
on the node; it owns cancellation by re-reading the row before `task_cancel`
so a Stop/timeout can't orphan the remote task, and never masks a
substrate-cancel as success), while `site=` + `background=True` defers through
the poll loop + continuation for long steps. `WeftSubmitter` picks the transport by
`site_contract()` (`weft_submitter.py`): shared-fs (host-less deployment-
declared sites — the unchanged lane above) vs **detached** (host-bearing or
ad-hoc sites — never guess shared-fs). Detached submit
(`_build_detached_task`) ships code AS DATA: a payload dir
{`detached_entry.py` — stdlib-only, language-agnostic harness that runs the
user script as a subprocess, ENFORCES `spec.timeout_s` (the only wall
enforcement on ssh sites, which have no scheduler walltime) and writes
`result.json` — user script, spec + job-id **memo nonce**} →
`data_register(ingest=True)` → staged task input (the staging dir is
deleted after registration so spec.json never surfaces as a Run output);
command `python3 payload/aba_entry.py` under the env's prefix when
`env=EnvID` rides along (weft realizes the env at the site). A platform
mismatch (weft surfaces it at REALIZE, async) triggers ONE lazy re-lock
(`named_envs.ensure_platform` — replays the env AS BUILT from its persisted
base spec + extend layers, keeping a python pin) + transparent resubmit at
the poll side. `_poll_detached` reads `result.json` + small outputs over
the data plane into the local run dir → the standard controller-side
harvest; large outputs stay on the node (kept / `(run,rel)` / bring-back).
Env-less runs stamp `env_grade: node-system` + runtime. Sync rows are BORN
`sync` (before the substrate submit — no poll-loop adoption window); a
submit that dies on the substrate marks the row `failed` (a stale 'queued'
row would restart-reconcile onto the LOCAL worker). **Walltime doctrine
(`_sized_walltime`, ONE policy for both transports):** an explicit walltime
is asked only for SIZED jobs (agent estimate given; timeout+300) — an ask
inflated from the default timeout pends forever under `PartitionTimeLimit`
(verified live); unsized jobs ride the partition default; nextflow heads
are sized by design (their timeout IS the chosen head walltime). Tests:
`tests/test_detached_lane.py`, `test_detached_agent_inputs.py`,
`test_detached_cluster.py` (opt-in docker e2e), `regtest/datasets/multinode.py`
(live agent).

## Known gaps

- **Body drift above the W2 section.** §"The model" and the implementation table still
  describe the RETIRED sbatch `SlurmSubmitter` as live (W3.5 deleted that lane; the cluster
  path is `WeftSubmitter(site=<slurm site>)`). The weft-era sections at the bottom are
  current; the top half needs a consolidation pass.
- **`runner.py` is a ~1300-line god-module** fusing five responsibilities: the local async
  worker, the submit API (`submit_*_job`), finalize+continuation dispatch, restart
  reconcile/reap, and the Slurm poll + inline watchdog loops. `LocalSubmitter` living here
  (not beside `SlurmSubmitter`) forces `get_submitter`'s lazy import to dodge a cycle. The
  clean shape is a `worker.py` / `local_submitter.py` / `lifecycle.py` split.
- **`guide → core.jobs` down-edge remains (up-edge dissolved).** The Compute→Reasoning
  *up*-edge is fixed (modularity_audit3 Item 1, Phase 1): `continuation._fire` re-enters via
  `core/reasoning_port.run_continuation` (guide registers the handler at import) instead of
  `from guide import stream_response`, and `check_seam.sh` rule 4 now forbids core importing
  guide. What's left is the *down*-edge: `guide.py:27` still imports the concrete
  `submit_python_job` from `core.jobs.runner` — the guide *should* submit through a
  content-registered interface (Item 1 / Phase 2b). A one-way layering gap, not two-way.
- **Single sequential local worker.** One in-memory `asyncio.Queue`, one job at a time,
  in-process — a restart is survived by reconcile but concurrency and cross-process durability
  wait on the arq+Redis successor.
- **Install-time GPU verify & build-on-target are only partially covered.** Per-job
  `gpu_capability_ok` verifies at *run* time, but ABA does not yet confirm at *install* that
  the built CUDA runtime initializes on each GPU partition, nor build node-arch-specific
  artifacts on the target partition. Owned by [`envs.md`](envs.md) (see its Known gaps) —
  cross-linked, not duplicated here.
