# Compute execution — running code now

How ABA runs the agent's Python and R *interactively* — the persistent kernel
pool behind `run_python`/`run_r`, and how outputs are harvested back into the
entity graph no matter where the code wrote them.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

The agent writes code the way a scientist writes a notebook: load an
`AnnData`/`DESeqDataSet` once, then plot, subset, and refit against it across
many calls. Run-now must make that cheap **without** letting one investigation's
state leak into another, and without ever silently losing a result. Everything
below derives from a few invariants:

- **Run-now sits behind a swappable `KernelSession` seam.** The pool and every
  caller speak `execute`/`interrupt`/`shutdown` (`core/exec/kernels/base.py:10`);
  no `jupyter_client` specifics leak past it. So kernels can move off-box (a
  remote gateway/E2B impl) with no change above the seam.
- **Never evict a busy kernel.** A kernel executing a cell is another thread's
  live analysis; culling it to reclaim a slot destroys that work. Eviction and
  the idle reaper skip any session marked `busy`; at the hard cap we *refuse*
  (`KernelCapacityError`) rather than kill running work.
- **Fail loud on kernel death; never drop output.** A kernel that dies mid-cell
  leaves its caller blocked on a reply that never comes — a hung turn pegging
  CPU. A watchdog detects the dead process, resets the session, and fails the
  turn with an actionable message instead of hanging or spinning.
- **Harvest is path-agnostic.** A figure or table is a result wherever the code
  saved it. Harvest scans the cell's working dir *and* the artifact store *and*
  the project work dir, so an off-convention `savefig('/…/work/fig.png')` still
  registers. The failure this prevents is the worst kind: the agent correctly
  reports `rc=0` + "figure saved", but the harvester loses it (`plots: []`) —
  which reads as fabrication (the "A2"/C4 incident).
- **Size thread pools to the allocation, not the hardware.** On a node allocated
  1 of 56 CPUs, an uncapped OpenBLAS spawns 56 threads and dies on the per-user
  process limit (`pthread` `EAGAIN`). BLAS/OMP pools are sized to the *allocation*.
- **Placement is ABA's decision, never the tool's:** declare → **decide** →
  place → run. Local mode never auto-backgrounds a cell (relocating a
  state-dependent cell into a fresh process silently loses its objects).

The **environment** a run executes in (immutable base + per-project overlay,
isolated envs, `ensure_capability`, the ABI anchor) is owned by
[`envs.md`](envs.md) — this doc consumes it and does not re-explain it.
Run-**later** (background jobs, Slurm/OOD submission, continuation) is owned by
[`jobs-and-hpc.md`](jobs-and-hpc.md). The **exec record** each run emits is owned
by [`provenance.md`](provenance.md).

## The model

Two execution lanes, one harvester:

```
run_python/run_r ─► LocalRouter.decide() ─► "local"  ─► KernelPool.get_or_start
 (bio/tools/run_exec.py)                    │           → JupyterKernelSession.execute
                                            └► "background" ─► submit_*_job (jobs-and-hpc.md)
                                                              (fresh process)
    both lanes ─► harvest_artifacts(cwd) ─► plots/tables/files + exec record
                 ─► on_post_tool hook ─► entities (entity-model.md)
```

- **`KernelPool`** — one process-wide pool (`core/exec/kernels/pool.py:21`) of
  live sessions keyed by `(scope_key, lang)`. `scope_key` is the **thread** (a
  line of inquiry) — or a sub-agent/scenario run id, or `thread::env::<name>` for
  an isolated-env kernel. State is shared within one investigation, isolated
  across them.
- **`KernelSession`** — the transport-agnostic interface; today's only impl is
  **`JupyterKernelSession`** (`core/exec/kernels/jupyter.py:405`), an
  out-of-process IPython/IRkernel driven over `jupyter_client`. State persists
  across `execute` calls; `interrupt` maps to SIGINT (state survives); a crash is
  isolated from the backend.
- **The stateless one-shot** — `run_python_code`/`run_r_code`
  (`core/exec/run.py:22`) write a self-contained `script.py`/`script.R` and run
  it via `MaterializingExecutor` (base venv + tools-env PATH, killpg
  cancellation). This is the `fresh=true` lane and the body of every background
  job, so a backgrounded run inherits the same overlay, harvest, and cancellation
  as run-now.
- **`harvest_artifacts`** (`core/exec/run.py:307`) — the single harvester both
  lanes call.

A sub-agent, scenario, or branch recompute gets its own `scope_key` (never the
thread's), so concurrent agents can't share a single-consumer kernel.

## The kernel pool (conservative lifecycle)

A kernel is expensive, long-lived state ABA owns, so the pool is deliberately
frugal:

- **Lazy start.** No kernel exists until the first interactive `run_python`/
  `run_r` in a thread; `get_or_start` spawns on demand and returns the live one
  otherwise (`pool.py:31`).
- **Per-user soft cap + LRU** = `KERNEL_MAX_LIVE` (5). Starting one over the cap
  evicts the least-recently-used **idle** session — never a busy one
  (`pool.py:47`). When every over-cap session is busy, the pool bursts above the
  soft cap up to `KERNEL_HARD_MAX` (`MAX+3` = 8); past that it raises
  `KernelCapacityError` and the tool returns `at_capacity` rather than killing
  running work (`pool.py:53`, surfaced at `run_exec.py:708`).
- **Idle reaper.** A daemon thread (`_start_reaper`, `pool.py:115`) culls sessions
  idle past `KERNEL_IDLE_TTL_S` (1 h) every 60 s; next use cold-starts. `busy`
  is protected because `last_used` is touched throughout a long run
  (`jupyter.py:593`).
- **Bounded startup.** `get_or_start` holds the pool lock across the whole
  session constructor, so a startup that *blocks* under resource pressure would
  wedge every other kernel request. `_start_bounded` (`jupyter.py:448`) caps
  `start_kernel`/`start_channels`/`wait_for_ready` (60/30/60 s) and fails fast,
  releasing the lock, rather than hanging forever.
- **Orphan hygiene.** Kernels are child processes that outlive a crashed uvicorn
  worker (reparented to init) and leak GBs. Two layers reap them: an `atexit` +
  FastAPI-shutdown `shutdown_all`, and a startup `_reap_orphan_kernels`
  (`pool.py:157`) that SIGKILLs only processes proven ours (our uid, our launch
  shape, a live connection file, an orphaned ppid chain). `kernel_pid()`
  resolves the pid across `jupyter_client` 8.x provisioner shapes
  (`jupyter.py:715`) — the accessor whose earlier gap let orphans accumulate
  unreapable.

### The dead-kernel watchdog

`execute` runs the blocking `execute_interactive` in a worker thread while the
calling thread polls (`jupyter.py:586`). Each 0.2 s tick it flushes pending
output, refreshes `last_used`, checks the cancel token, and calls
`kernel_dead()` — a cheap `Popen.poll()`/`os.kill(pid, 0)`, no zmq heartbeat
(`jupyter.py:728`). If the process exited mid-run, the watchdog stops waiting,
shuts the session down (unblocking the daemon worker on its dead channels), drops
it so the next call spawns fresh, and returns a failed `ExecResult` naming the
likely cause (killed / crashed / OOM) — **loud, not a silent hang**
(`jupyter.py:616`). `busy` is set for the duration and cleared in `finally`, so
the watchdog window is exactly the never-evict-busy window.

Cancellation escalates: a Stop registers `interrupt` (SIGINT, state-preserving);
if the cell ignores it (`ABA_KERNEL_CANCEL_GRACE_S`, default 3 s) the session is
hard-killed so an abandoned cell can't corrupt the next one (`jupyter.py:633`).

## run_python / run_r — the entry and the router

The agent-facing tools (`content/bio/mcp_servers/aba_core/tools/run_exec.py`)
delegate to one impl per language (`content/bio/tools/run_exec.py:611`,`:818`).
Each resolves the project + thread, then selects a lane —
**background > fresh > interactive** (`run_exec.py:655`):

- **Router.** `LocalRouter.decide()` (`core/exec/router.py:44`) reads the live
  `compute_env()` and the agent's estimate. In **local** mode it *never*
  auto-backgrounds — a long cell just raises `timeout_s` and runs interactively;
  background happens only on the explicit `background=True`. In **slurm** mode it
  additionally routes to background as a safety net when the step won't fit
  (cores/mem/GPU) or would exceed remaining walltime. A `"background"` choice
  hands off to `submit_*_job` ([`jobs-and-hpc.md`](jobs-and-hpc.md)); interactive
  goes to the pool.
- **Interactive path.** `get_or_start(scope_key, lang, cwd=…)` →
  `_ensure_kernel_cwd` re-points the kernel into the active Run's output dir →
  `sess.execute(code, cancel_token, timeout_s)` → `harvest_artifacts(cwd,
  since_ts=start_ts)` → an exec record ([`provenance.md`](provenance.md)) → a
  namespace preview + the workspace-orientation preamble. A first-start failure
  hard-resets and retries the kernel **once**, then degrades to the stateless
  one-shot with a loud `kernel_warning` so the agent knows state and cwd no
  longer persist (`run_exec.py:777`).
- **Resolved-`DATA_DIR` surfacing.** Both the kernel setup cell
  (`_setup_code`/`_r_setup_code`, `jupyter.py:236`,`:172`) and the subprocess
  preamble inject `DATA_DIR`/`WORK_DIR` into the namespace, and `_kernel_env`
  exports them as real env vars so `os.environ[...]`/`Sys.getenv(...)` resolve
  identically (`jupyter.py:353`). On a cwd shift or a fresh kernel, the
  orientation preamble prepends a banner to stdout naming the **resolved**
  `DATA_DIR` and the input files actually on disk — so the agent doesn't
  conclude "no data, ask the user to upload" when files are present but
  unregistered (`run_exec.py:386`).
- **Output cap + stream coalesce.** stdout/stderr are middle-snipped to
  `TOOL_OUTPUT_CAP_CHARS` (50k) at result-build time — head+tail kept, middle
  replaced by a recovery marker (scientific output is informative at both ends,
  `core/exec/output_cap.py:27`). Live iopub chunks are batched by a `Coalescer`
  into 10 KB / 0.5 s bursts for the output drawer, plus a throttled one-line
  progress tick — millisecond chunks would only flood the wire
  (`core/exec/stream_coalesce.py`, `jupyter.py:479`).
- **CPU/BLAS thread pinning.** `_kernel_env` sets `OMP/MKL/OPENBLAS/NUMEXPR/…
  _NUM_THREADS` at kernel launch (torch/numpy read them at import) to
  `default_thread_cap()` (`core/exec/cpu.py:100`): the explicit allocation
  (Slurm/cgroup/`ABA_CPU_LIMIT`) honored in full, else `min(cpu, 8)` on an
  unscheduled box. `pin_blas_threads()` sets the same process-wide at startup so
  every child (kernels, `Rscript`, micromamba) inherits a sane cap.

`run_python` and `run_r` are near-parallel across both the entry impl and the
stateless core; the shared machinery (router, harvest, exec record, preamble) is
factored out, but the two language bodies are deliberately **not** collapsed into
one `LangSpec` (see Known gaps).

## Path-agnostic artifact harvest

`harvest_artifacts(scratch, since_ts, project_id)` (`core/exec/run.py:307`) turns
files a run left behind into `(plots, tables, files, warnings)`; the caller's
`on_post_tool`/`on_job_complete` hook then registers them as figure/table/file
entities with provenance ([`entity-model.md`](entity-model.md),
[`provenance.md`](provenance.md)). The design is *capture wherever it landed*:

- **Recursive scan of the working dir** (`rglob`, skipping caches/checkpoints),
  mtime-filtered by `since_ts` so a persistent kernel's earlier cells aren't
  re-harvested. Recursion is load-bearing: a recipe that writes per-sample plots
  into subdirs (pagoda2's `pagoda2_<sample>/qc_*.png`) surfaces them, not just
  the top level (`run.py:273`).
- **Off-convention passes** (`_capture_external`, `run.py:445`), gated on
  `since_ts` (the session lane) and non-recursive, catch files written *during*
  this exec into (A/B) the **artifact store dir** (`savefig('/artifacts/<pid>/…')`)
  and (C4) the **project work dir** (`savefig('<project>/work/…')`) — the parent
  of the per-thread cwd. Without these, an agent's absolute-path save returns
  `rc=0` yet the figure is orphaned on disk: the apparent-fabrication bug this
  invariant exists to kill.
- **Buckets by kind.** `*.png`/`*.jpg` → `plots` (inline); `*.csv`/`*.tsv` →
  `tables` (viewers); everything useful else → `files` (hashed copy served at
  `/artifacts/<pid>/<hash><ext>`). A **single-page PDF** is promoted to `plots`
  (it's a figure) with a rasterized `preview_url`; multi-page PDFs stay `files`.
  Files over 50 MB are link-only, reported in warnings, never auto-copied
  (`run.py:405`).
- **Fail loud on a failed plot.** A PNG that is one flat colour — matplotlib
  saved an empty/never-drawn canvas — is **dropped** and reported in warnings
  telling the agent the plot *failed* (check n_obs, the embedding, savefig
  ordering), so a white box is never presented as a result (`_png_is_blank`,
  `run.py:519`).

## Key implementation references

| Where | What |
|---|---|
| `core/exec/kernels/base.py` | `KernelSession` protocol — the transport-agnostic seam |
| `core/exec/kernels/pool.py` | `KernelPool`: lazy start, LRU + never-evict-busy, hard cap, idle reaper, orphan reaper, `KernelCapacityError` |
| `core/exec/kernels/jupyter.py` | `JupyterKernelSession`: bounded startup, `execute` worker + dead-kernel watchdog, `busy` flag, setup cells, `_kernel_env` thread pinning, kernelspec management |
| `core/exec/run.py` | stateless `run_python_code`/`run_r_code` (fresh + background body); `harvest_artifacts` (path-agnostic scan, blank-PNG + PDF handling) |
| `content/bio/tools/run_exec.py` | agent-facing impl: lane selection, interactive kernel drive, exec record, namespace preview + `DATA_DIR` orientation preamble |
| `content/bio/mcp_servers/aba_core/tools/run_exec.py` | the `run_python`/`run_r` MCP tool surface (docstrings, placement estimate params) |
| `core/exec/router.py` | `LocalRouter.decide()` — interactive-vs-background placement |
| `core/exec/compute_env.py` | `compute_env()` + `context_line()` — the per-turn "Compute environment:" cue the router + agent read |
| `core/exec/cpu.py` | `default_thread_cap`/`effective_cpu_count`/`pin_blas_threads` — size to the allocation |
| `core/exec/output_cap.py` · `stream_coalesce.py` | middle-snip cap; live iopub coalescing |

## Known gaps

- **`run_python`/`run_r` duplication.** The two language paths run near-parallel
  bodies in both `run_exec.py` and `run.py`. A `LangSpec` collapse is
  **consciously deferred**: the functions
  genuinely diverge (kernelspec, preamble, libpaths, future/BLAS env), it's the
  critical path, and it's hard to verify safely without a live kernel. Kept
  in sync by hand today.
- **Intermittent kernel hangs under load.** Despite the watchdog and bounded
  startup, heavy concurrent load has produced turn-timeouts / apparent kernel
  wedges. The bounded-startup caps address the *pool-lock-held-during-startup*
  wedge specifically; a lingering worry is unbounded contention when many
  threads request kernels at once (single-consumer per session, but pool
  acquisition serializes on one lock).
- **`display_data` images aren't captured inline.** Rich `execute_result`/
  `display_data` payloads are folded into stdout text; a plot shown but never
  `savefig`'d in a kernel cell isn't registered.
  Recipes/agents are steered to write files (or `harvest_table`) for anything
  that should become an entity.
