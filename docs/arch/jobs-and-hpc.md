# Background jobs & HPC

How ABA runs work that outlives a single agent turn — a long `run_python`/`run_r`/
`run_nextflow` — as a **background job**, on this node or offloaded to a Slurm cluster,
and how the finished job wakes the agent back up.

> Status: current as of 2026-07. This is the **maintained** reference; the design/
> evolution log lives in `misc/hpc_jobs.md` (the Slurm job system) and
> `misc/deferred_jobs.md` (lifecycle/completion/restart-recovery), with
> `misc/cluster_modules.md` (module provider) and `misc/inplace_submission.md` (in-place)
> as satellites.

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
                                   enqueue_continuation → _fire → guide.stream_response
                                        (a fresh turn resumes the agent's plan)
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
(`misc/hpc_jobs.md`). A per-job `CancelToken` keyed by `job_id` lets `cancel_job` killpg the
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
sentinel. (Two watchdogs run alongside: `_slurm_poll_loop` for external jobs and
`_inline_watchdog_loop` (`runner.py:1229`) for wedged inline pipeline heads.)

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
project, then re-enters the agent via `guide.stream_response` on a fresh `run_id`: a new
durable turn, exactly as if the user had typed. The trigger is a **synthetic user message**
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
`hpc:` settings → **live auto-detection**. When nothing pins them, partitions come from live
`sinfo` (default partition first) and QOS+account from live `sacctmgr`
(`slurm_live.qos_account_live`, ranked most-permissive first, carrying the primary QOS's
`MaxWall` as a walltime cap). So an unconfigured cluster still routes GPU/large jobs to real
partitions and submits the right `--qos`/`--account` with no `hpc.yaml` at all — the file is a
pure optional override. `slurm_live.py` is also the read model behind the agent's
`describe_compute` surface (partitions, node sizes, queue depth, wait hints).

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
| `core/jobs/hpc_config.py` | `hpc_config` (config → bundle → live sinfo/sacctmgr); `resolve_resources` (estimate → partition/qos/account/walltime) |
| `core/jobs/slurm_live.py` | live `sinfo`/`squeue`/`sacctmgr` parsers; `qos_account_live`; `describe_compute` read model |
| `core/jobs/continuation.py` | `enqueue_continuation` (defer/fire); `_fire` → `guide.stream_response`; per-outcome `_continuation_message_text` |
| `core/graph/jobs.py` | the `jobs` table: `create_job`/`update_job`/`get_job` |
| `content/bio/tools/run_exec.py` · `guide.py` | callers of `submit_*_job` (background routing; the estimate) |
| `misc/hpc_jobs.md` · `misc/deferred_jobs.md` | design/evolution logs (Slurm system; lifecycle+recovery) |

## Known gaps

- **`runner.py` is a ~1300-line god-module** fusing five responsibilities: the local async
  worker, the submit API (`submit_*_job`), finalize+continuation dispatch, restart
  reconcile/reap, and the Slurm poll + inline watchdog loops. `LocalSubmitter` living here
  (not beside `SlurmSubmitter`) forces `get_submitter`'s lazy import to dodge a cycle. The
  clean shape is a `worker.py` / `local_submitter.py` / `lifecycle.py` split; `misc/modularity_audit3.md`
  flags it.
- **`guide ⇄ core.jobs` is a two-way import coupling.** `continuation.py:472` imports
  `guide.stream_response` (the compute plane reaching into the reasoning plane) and
  `guide.py:27` imports the concrete `submit_python_job`. Both are lazy/at-module-scope
  workarounds, not a seam — the continuation *should* re-enter the loop through a
  reasoning-plane port, and the guide *should* submit through an interface. An honest layering
  gap.
- **Single sequential local worker.** One in-memory `asyncio.Queue`, one job at a time,
  in-process — a restart is survived by reconcile but concurrency and cross-process durability
  wait on the arq+Redis successor (`misc/hpc_jobs.md`).
- **Install-time GPU verify & build-on-target are only partially covered.** Per-job
  `gpu_capability_ok` verifies at *run* time, but ABA does not yet confirm at *install* that
  the built CUDA runtime initializes on each GPU partition, nor build node-arch-specific
  artifacts on the target partition. Owned by [`envs.md`](envs.md) (see its Known gaps) —
  cross-linked, not duplicated here.
