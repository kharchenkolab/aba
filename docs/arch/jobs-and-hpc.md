# Background jobs & HPC

How ABA runs work that outlives a single agent turn — a long `run_python`/`run_r`/
`run_nextflow` — as a **background job**, on this node, a remote machine, or a Slurm
cluster, and how the finished job wakes the agent back up.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

Interactive `run_python` is owned by [`compute-execution.md`](compute-execution.md) (the
kernel pool, run-NOW, artifact harvest). This doc owns the **LATER** path: a step too
long or too heavy for the live kernel becomes a durable **job**. The routing decision
(interactive vs background, and the `estimated_runtime_min` that drives it) lives in the
run tool and is summarized there; a job's life begins once `submit_*_job` is called.

Four invariants shape everything here:

- **Placement is pluggable behind one protocol.** *Where* a job runs — a weft task on this
  node, on a remote or Slurm site, or the in-process fallback — is a `BatchSubmitter` chosen by
  `get_submitter`, never an `if slurm:` sprinkled through the runner. The job **row** is
  created identically for every submitter; the submitter only decides how it *runs* and
  how its status/cancel/monitor resolve. **Failure it prevents:** a new backend (a different
  substrate or scheduler) forcing edits across the whole lifecycle instead of one class.
- **A finished job re-enters the reasoning loop.** The agent turn that submitted the job
  already ended, so its planned downstream steps (UMAP after training, QC after a
  pipeline) would never run. **Every** terminal transition fires a **continuation**: a
  synthetic turn on the originating thread that resumes the plan. **Failure it prevents:**
  a completed job that silently produces artifacts nobody acts on — a dead-ended plan.
- **Certainty across nodes = discover-once + verify-at-use.** ABA runs on a login/CPU node
  that **cannot observe** the compute node a job lands on. So the submit side *discovers*
  the landscape (through the weft SitePort) and *asserts* a request; the compute side **verifies
  at the point of use** (GPU present, numpy importable) and fails loud if the assertion was
  wrong. **Failure it prevents:** a GPU job silently training on CPU on an idle allocated
  GPU (the scVI-on-CPU incident).
- **Job env hygiene is non-negotiable.** A background job runs the **same** weft-realized env
  as an interactive run (a `session_snapshot` EnvID, or a named env's EnvID — owned by
  [`envs.md`](envs.md)); nothing on the node may shadow that interpreter. Correctness comes from
  weft's env **activation** — `slurm_entry` reads `$CONDA_PREFIX` (`_interp_from_activation`), and
  python/R jobs do **no** cluster `module load` at all — backstopped by a **numpy import canary**.
  **Failure it prevents:** an ancient cluster-module Python breaking `import numpy` deep in the
  user's code (the prj_6d986f40 incident).

## The model

A **job** is a row in the per-project `jobs` table (`core/graph/jobs.py`) — a `kind`
(`run_python`/`run_r`/`run_nextflow`/`import_run`), a `status`
(`queued→running→done|failed|cancelled`), and a `params` blob that carries everything the
worker needs decoupled from live process state: the `code`, the captured `project_id` /
`thread_id` / `run_id`, the agent's `estimate`, the resolved `submission` target, and (once
dispatched) the `weft_id` / `weft_site` / `run_dir` / `resources`. Capturing project+thread+run
**at submit time** is what lets the job attach its outputs to the right Run even after the active
project changes.

A **`BatchSubmitter`** (`core/jobs/submitter.py`) is the pluggable placement — a Protocol of
`submit`/`cancel`/`poll`/`info`. The live implementation is **`WeftSubmitter`**
(`weft_submitter.py`): it runs the job as a **weft task** — on this node or on a declared
remote/cluster site — and resolves completion by polling the substrate. It carries **two
transports**, chosen by the site's contract (`site_contract`): **shared-fs** (controller and node
see the same paths — the local node and shared-filesystem Slurm clusters) writes
`job_spec.json`/`result.json` into a scratch dir the node reads directly, and the node runs the
same `python -m core.jobs.slurm_entry` entry an interactive run uses; **detached** (a host-bearing
ssh site that shares nothing) ships the code *as data* over the weft data plane and runs a
stdlib-only payload harness. `get_submitter()` maps the `config.env` toggle
`ABA_BATCH_SUBMITTER=local|slurm` (a topology fact exactly like `ABA_ACCELERATOR`, owned by
[`deployment-and-access.md`](deployment-and-access.md)) onto a lane: `local` → a **local-site weft
task**; `slurm` → a weft task on the declared **Slurm-kind site** (`_slurm_lane` →
`WeftSubmitter(site=…)` via `weft_slurm_site()`, never `sbatch`). With the substrate offline the
submit fails with the substrate's own **typed error** — an honest refusal (science jobs cannot run
without their envs anyway); the legacy silent fall-back to an in-process worker and the `worker`
escape hatch were **retired with the kernel-transport cutover**. `LocalSubmitter` (`runner.py`)
still exists, but only as the **cancel owner** for a legacy pre-cutover row that recorded an
in-process `inline` submission — never a submit-time lane.

```
run_python/run_r(background) ─ submit_*_job ─► create_job(row, status=queued)
                                                    │  get_submitter_for(submission)
                       ┌────────────────────────────┴──────────────────────────────┐
             WeftSubmitter.submit  (a weft task)                  substrate offline →
               ├ shared-fs (local node · Slurm site):             typed refusal at submit
               │   python -m core.jobs.slurm_entry <spec> → result.json   (no in-process fallback)
               └ detached (ssh site): code-as-data payload →
                   python3 payload/aba_entry.py → result.json (data plane)
                       │  _weft_poll_loop watches for a terminal state
                       └───────────────► _finalize_job(result)
                                             │  exec record → on_job_complete (harvest) → status=done|failed
                                             ▼
                                   enqueue_continuation → _fire → reasoning_port.run_continuation
                                        (→ guide's registered handler; a fresh turn resumes the plan)
```

The seam is that **every** lane converges on one `_finalize_job` (`runner.py`): map
result→status, write the run log, stamp the provenance exec record, register artifacts via the
`on_job_complete` hook, then fire the continuation. The transports differ only in how the result
*arrives*.

## The worker, the poll loop, and restart survival

**In-process fallback (`LocalSubmitter`).** When the substrate is offline (or
`ABA_BATCH_SUBMITTER=worker`), a single async worker (`_worker`, `runner.py`) pulls
`(job_id, project_id)` tuples off an in-memory `asyncio.Queue` and runs them **one at a time**
through `_run_one` — the same execution core as synchronous `run_python`, so the job sees the
project's weft env and killpg cancellation. It is deliberately single-process and sequential — a
loud fallback, not the durable path; a restart is survived only by reconcile (below). A per-job
`CancelToken` keyed by `job_id` lets `cancel_job` killpg the whole process group.

**Weft (the durable path).** Every other lane is a **weft task**, watched by a single
`_weft_poll_loop` (`runner.py`, always started; it idles while the substrate is offline). There
are **no callbacks or `sbatch` sentinels**: the loop scans active weft rows, calls the
submitter's `poll()`, and routes any terminal result through the same `_finalize_job`.
`WeftSubmitter.poll()` re-reads the persisted row and derives its transport from the site's
declared contract, so a caller's stale dict can't misroute a detached task into the
controller-local `result.json` check. A terminal-DONE task whose `result.json` isn't readable yet
gets bounded grace retries (data-plane / NFS visibility lag), never an empty result — returning
the empty fallback once silently dropped a whole run's artifacts. The two transports (how the task
is built, and how its result is fetched back) are detailed in the weft-lane sections below; an
`_inline_watchdog_loop` runs alongside for wedged inline pipeline heads.

**Restart survival.** Because ABA is a single process, a restart must reconcile state.
`reconcile_jobs` (`runner.py`), run once at startup **before** the worker drains the
queue, sweeps every DB that can hold job rows — each project DB, or in SINGLE mode
(`ABA_DB_PATH`) the one flat workspace DB (the walk alone sees zero projects there and
recovery would silently not run — found live by `regtest/datasets/restart_study.py`).
Local `running` rows are **zombies** (their worker died with the process) → marked
`failed` (except an inline Nextflow head under the resume cap, which is re-queued with
`-resume`); `queued` rows are re-enqueued in global `created_at` FIFO. The pivot is
`_is_slurm_params`: a **Slurm/remote** weft task keeps running across the
restart, so reconcile must **not** reap or re-enqueue it — the weft poll loop re-adopts it from
its persisted task id. **Sync weft rows** (owned in-tool by `_run_remote_sync`'s wait loop, which the
background poll loop deliberately skips) lose their only finalizer with the process:
reconcile **adopts** substrate-accepted ones into the poll loop (`sync` flipped off,
`sync_orphaned` stamped) and reaps ones that never reached the substrate. **Local-lane weft
rows** are a third species: the substrate task's supervisor lived in-process (so weft's
state row can freeze non-terminal — misc/bug3), but the task PROCESS survives the kill and
its entry still writes `result.json`. Reconcile only **stamps** them (`local_orphan_at` —
never kills: the task may be mid-run); the poll loop then finalizes a stamped row from
`result.json` disk truth even while weft's state is frozen, and past walltime+grace issues
an honest orphan verdict (with a best-effort `task_cancel`). (Two loops run
alongside: `_weft_poll_loop` for weft tasks and `_inline_watchdog_loop`
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

`get_submitter_for(target)` (`submitter.py`) is the per-job override that makes placement finer
than a deployment default: `'inline'` → `_local_lane` (a local-site weft task, or the in-process
`LocalSubmitter` when the substrate is down), `'slurm'` → `_slurm_lane` (`WeftSubmitter` on the
declared Slurm-kind site), anything else → the default. `resolve_submission_target` (`submit.py`)
decides, at submit, whether a *local-executable* job (`execution` in `local`/`auto`) actually fits
ABA's free allocation — routing it to the cluster site when ABA is on a login node, when the
heaviest task exceeds free cores/mem, or (for `auto`) when the job is substantial enough to be
worth fanning out. This is why cancel routes through `_submitter_for_job` keyed on the row's own
lane (`params.submission` / `weft_id`), **not** the deployment default: a small job may still have
run inline, and cancelling it as a cluster task with no weft id would orphan the live inline
process. The nesting is intentional: on a cluster ABA itself runs inside a Slurm allocation and
dispatches **further** work as weft tasks on the cluster's nodes.

ABA emits **no scheduler flags of its own.** It maps the agent's **estimate** (`runtime_min` +
optional `cores`/`mem_gb`/`gpu`) to a plain resource request — `{cpus, mem_gb, gpus, walltime}` —
that rides the weft task; **weft** translates it into the site's `--partition` / `--qos` /
`--account` / `--gres` at submit. The sizing reads the site's live landscape (partitions +
limits, QOS walltime caps) back through the weft SitePort (below), clamping the request to what
fits; a heavy pipeline head is sized from the site's Nextflow config, not the task estimate.

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
`compute_env._cluster_landscape` (partitions, node sizes, queue depth, wait hints). *(A slurm
site's live queries run through weft, not a local `sinfo`/`squeue`/`sacctmgr` read model. One
behavior note: weft's capability record carries no default-partition marker, so
`_live_partitions` does not order the default partition first.)*

**Hygiene + verify-at-use (compute side).** The node runs `python -m core.jobs.slurm_entry` under
the env **weft activated** — `slurm_entry` takes its interpreter from `$CONDA_PREFIX`
(`_interp_from_activation`), so the env's own stdlib+site-packages win with no `module load` to
shadow them (python/R jobs load **no** cluster modules; only a Nextflow head loads its own
`nextflow` module, node-side in `core/exec/nextflow.py`). For the shared-fs lane `WeftSubmitter`
also pins `PYTHONPATH` into the task env so the backend is importable; the detached lane sets none
(its harness is stdlib-only). Inside, `slurm_entry.main` runs the code through the same
`run_python_code`/`run_r_code` core (artifacts harvest identically), guarded by two preflights: a
**numpy canary** (`verify_python_imports(["numpy"])`) that fails loud if the wrong interpreter got
picked, and a **GPU verify-at-use** — when the agent requested a GPU, `gpu_capability_ok()` must
confirm a usable CUDA torch on *this* node or the job aborts actionably rather than burning the
allocation on CPU. Both helpers, and the CPU-vs-CUDA base they check against, are owned by
[`envs.md`](envs.md); this doc owns their use *at the job boundary*.

## Key implementation references

| Where | What |
|---|---|
| `core/jobs/submitter.py` | `BatchSubmitter` Protocol; `get_submitter` (`ABA_BATCH_SUBMITTER=local\|slurm\|worker`) → `_local_lane`/`_slurm_lane`; `get_submitter_for` (per-job inline/slurm override); `weft_slurm_site`/`site_contract` |
| `core/jobs/weft_submitter.py` | `WeftSubmitter` — the live submitter: shared-fs + detached transports (`site_contract`), `_build_detached_task`, `poll`/`_poll_detached`, `_compute_block` (placement stamp), `task_cancel` |
| `core/jobs/submit.py` | `submit_python_job`/`submit_r_job`/`submit_nextflow_job`/`submit_import_run_job` (the submit API); `resolve_submission_target`; `_bg_submission` |
| `core/jobs/runner.py` | `LocalSubmitter` (in-process fallback worker); `_run_one`/`_worker`; the shared `_finalize_job`; `reconcile_jobs` + orphan reap; `_weft_poll_loop`; `_inline_watchdog_loop`; `cancel_job` |
| `core/jobs/slurm_entry.py` · `detached_entry.py` | compute-node entries: `slurm_entry` (shared-fs lane — weft-local + Slurm site; reads the weft-activated env off `$CONDA_PREFIX`; numpy canary + GPU verify-at-use); `detached_entry` (the stdlib-only payload harness the detached lane runs) |
| `core/jobs/hpc_config.py` | `hpc_config` (config → bundle → live weft SitePort); `_live_partitions`/`_live_qos_account` (weft `sites_describe`/`site_associations`) for routing + sizing |
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
routes by hard evidence (`params.weft_id` → `task_cancel`). On a non-slurm deployment an
unspecified `execution` resolves to this local weft lane, never a scheduler submit.

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

- **weft-side local-orphan liveness (misc/bug3_weft_local_orphan.md).** A local-lane task
  whose controller dies stays RUNNING in weft's own `state.db` forever (disk truth —
  `exit_code`, log — is never re-checked). aba's stamp+finalize mitigation keeps aba's rows
  honest, but weft-level surfaces show phantom RUNNING tasks until the weft fix lands.
- **`runner.py` is a large god-module** fusing the in-process fallback worker,
  finalize+continuation dispatch, restart reconcile/reap, and the weft poll + inline watchdog
  loops. `LocalSubmitter` living here (not beside `WeftSubmitter`) forces `get_submitter`'s lazy
  import to dodge a cycle. The clean shape is a `worker.py` / `local_submitter.py` /
  `lifecycle.py` split.
- **`guide → core.jobs` down-edge remains (up-edge dissolved).** The Compute→Reasoning
  *up*-edge is fixed (modularity_audit3 Item 1, Phase 1): `continuation._fire` re-enters via
  `core/reasoning_port.run_continuation` (guide registers the handler at import) instead of
  `from guide import stream_response`, and `check_seam.sh` rule 4 now forbids core importing
  guide. What's left is the *down*-edge: `guide.py:27` still imports the concrete
  `submit_python_job` from `core.jobs.runner` — the guide *should* submit through a
  content-registered interface (Item 1 / Phase 2b). A one-way layering gap, not two-way.
- **The in-process fallback worker is single & sequential.** The `LocalSubmitter` fallback (used
  when the substrate is offline, or forced by `ABA_BATCH_SUBMITTER=worker`) is one in-memory
  `asyncio.Queue`, one job at a time — a restart is survived by reconcile but its concurrency
  waits on the arq+Redis successor. The durable path is the weft lane, which does not share this
  limit.
- **Install-time GPU verify & build-on-target are only partially covered.** Per-job
  `gpu_capability_ok` verifies at *run* time, but ABA does not yet confirm at *install* that
  the built CUDA runtime initializes on each GPU partition, nor build node-arch-specific
  artifacts on the target partition. Owned by [`envs.md`](envs.md) (see its Known gaps) —
  cross-linked, not duplicated here.
