# Environments & provisioning

How ABA provides the software a run executes in — Python and R — and how it adds
packages on demand without corrupting shared state.

> Status: current as of 2026-07. This is the **maintained** reference; the design/
> evolution log lives in `misc/env_refactor.md` (§0 "as-built") and `misc/capabilities.md`.

## Aims & principles

The scientific stack is **ABI-fragile**: every compiled package (numpy, scipy, scanpy,
numba, torch…) is built against one numpy ABI. A single unpinned install can move numpy
and silently break `import` for unrelated code. So the model is **integrity-safe by
construction**:

- **Never mutate shared state unpinned/unverified/in-place.** Installs are pinned to the
  base ABI, verified by a real import (not just "is it present"), and land in a
  session-writable layer — never the shared foundation.
- **Give the agent real control** (add packages, isolate a conflict) **without** letting
  one project's install corrupt another or the base.
- **Fail loud, not silent.** A broken or unguarded environment is surfaced (startup
  self-check, `env_root_cause`), never left as a latent landmine.

## The model

**Python — two tiers + an escape hatch.**
- **base (immutable)** — the baked scientific stack (the install-wide foundation). Made
  read-only at runtime so nothing can mutate it; self-heals at startup (see Guards).
- **project overlay** (`ENVS_DIR/pylib_proj/<pid>`) — the only session-writable layer,
  **prepended** so a project's package version wins over base, with **numpy + the ABI
  core pinned** (`abi-anchor.txt`) so an override can't shadow-break the compiled stack.
  Per-project: one project can't pollute another or the base.
- **isolated envs** (`ENVS_DIR/isolated/proj/<pid>/<name>`) — the escape hatch for a hard
  conflict; a full separate env with its own persistent kernel, reproducible from a
  per-env lock.

**R — parity.** A per-project library (`r_libs/prj_<id>`) is prepended to `.libPaths()`;
base R lives in the conda tools env. Same integrity aims.

**Conda tools env** (`ENVS_DIR/tools`) — CLI tools (samtools, STAR, r-base…) installed via
micromamba, exposed to runs via **PATH only**. NB: this is for *executables*, not for
importable Python libraries (see Known gaps).

## Provisioning (adding a capability on demand)

Agent calls `ensure_capability(name)` → resolves a capability record → `materialize()`
dispatches by provisioning kind:
- **pip** → installs into the project overlay via `pip --prefix`, **`--prefer-binary`**
  (use a prebuilt wheel over a newer sdist — never source-build on an old system toolchain
  when a wheel exists for some version), **constrained to the ABI anchor** (numpy pinned to
  the base version). Two-phase: fast `--prefix`, then an `--ignore-installed` retry if the
  read-only base blocks an override.
- **conda** → micromamba into the shared tools env (CLI tools on PATH).

The **ABI anchor** is the crux of pip safety: `abi_anchor_constraints()` pins numpy to the
base's installed version so an overlay install reuses the prebuilt base numpy instead of
pulling/rebuilding it. The version is read from **live package metadata** (robust to a
conda-forge / local-wheel base, where `pip freeze` renders packages as `name @ file://…`
rather than `name==version`). A full base freeze (`ensure_base_constraints` → `_freeze_pins`,
also metadata-based) or a shipped canonical lock (`$ABA_BASE_LOCK`) backs the legacy
shared path.

## Integrity guards

- **Read-only base + startup self-heal** (`self_heal_base`): `pip check` + a deep import of
  the lazy workflow deps; repairs the missing closure from the lock; re-freezes read-only.
- **ABI-anchor pin** on every overlay install (above) — an incompatible-numpy override
  fails the resolve instead of shadow-breaking the stack.
- **`env_selfcheck()`** — a fast standard check (ABI anchor armed + numpy resolvable) run at
  startup; catches the *silent* config gap the deep closure check misses (e.g. the anchor
  being unresolved). Also a CI invariant (`tests/test_env_integrity.py`).
- **Real-import verification** — capabilities are confirmed by importing, not by
  `find_spec`; a present-but-unloadable (ABI-mismatched) package is caught, not reported ready.
- **Background jobs** run on the same base + overlay; `slurm_entry` clears `PYTHONHOME` +
  pins `PYTHONPATH` so a cluster module can't shadow the interpreter (see
  `misc/deferred_jobs.md`, the prj_6d986f40 incident).

## GPU / accelerator (target hardware)

A step's *hardware-variant* need (a CUDA build of torch vs the CPU build) is a distinct axis
from its *library* needs, and lives in a different tier:

- **Hardware variant → the base, chosen at install** (deployment-conditional). `torch` comes
  in transitively via `scvi-tools`; conda-forge's default is the **CPU-only** build. A GPU
  deployment builds a **CUDA** base instead. The decision is one toggle in
  `$ABA_HOME/config.env` — `ABA_ACCELERATOR=cpu|cuda` (+ optional `ABA_CUDA_VERSION`) —
  written by `install/linux/setup.sh` (auto-detects a `gpu` Slurm partition; admin-overridable)
  and applied by `install/core/inject-accelerator.sh`, which injects a `pytorch-gpu` pin into
  the copied `environment.yml` at `create-env` (single source — no duplicate GPU env file). A
  CUDA torch is a **superset**: it uses a GPU when present and falls back to CPU on the login
  node / CPU jobs, so one base serves both. Non-GPU deployments (laptops) build the CPU base.
  The base is **built on the GPU-less login node** (there is no build-time access to a GPU
  node): conda-forge `pytorch=*=cuda*` builds require the `__cuda` virtual package, which
  micromamba only detects from a host driver, so `create-env` exports `CONDA_OVERRIDE_CUDA`
  (default `11.8`; `ABA_CUDA_VERSION` overrides) to spoof it — this both unblocks the solve and
  selects the CUDA major (`12.x`→`cuda12x`, `11.8`→`cuda118`). `11.8` is the widest-compat
  default: it runs on any driver ≥450.80 and covers GPUs sm_60 (P100)…sm_90 (H100), so it
  survives older-driver / Pascal-Volta clusters where a `12.x` runtime would fail. The build is
  node-independent; the actual GPU is confirmed at job time (verify-at-use).
- **Non-torch GPU frameworks → overlays / isolated envs** (jax[cuda], RAPIDS) — the library
  axis, not the base.

**Certainty across nodes = discover-once + verify-at-use** (ABA runs on a CPU login node; a
job runs on a GPU node ABA can't observe):
- **`gpu_usable`** (`compute_env`) — a *node-independent* readiness hint in the agent's
  per-turn cue: a GPU is present (local or a `gpu` partition) **and** the base torch is a CUDA
  build (`torch_cuda_build` = `torch.version.cuda`, a property of the build, not of runtime
  GPU visibility). If a GPU exists but the base is CPU-only, the cue **warns** so the agent
  runs on CPU / tells the user instead of submitting a job that silently falls back.
- **Verify-at-use** (`slurm_entry`) — a GPU-requested job (`estimate.gpu`) is preflighted on
  the compute node via `gpu_capability_ok()`; if no usable GPU, it **fails fast** rather than
  training on CPU on an idle allocated GPU (the scVI-on-CPU incident: right placement, CPU base).
- **`env_selfcheck` invariant + `aba doctor`** — a deployment declaring `ABA_ACCELERATOR=cuda`
  must have a CUDA-build torch; a CPU-only base is flagged at startup / by `doctor`, which
  names the fix (set the toggle + rebuild the env).

**Config topology (no floating vars):** the accelerator toggle is a `config.env` line
(installer-written, admin-editable), exactly like `ABA_BATCH_SUBMITTER`. `hpc.yaml` stays
compute-topology (its `gpu: true` partitions are *detection input*, not a second home for the
toggle). The base spec (`environment.yml`) lives in the repo.

## Key implementation references

| Where | What |
|---|---|
| `core/config.py` | `RUNTIME_DIR`, `ENVS_DIR` (mutable-state roots + resolution) |
| `core/exec/materialize.py` | `materialize()` dispatch (pip/conda); `_pip_install` (overlay + constraints); `_conda_install` (tools env); `PYLIB_DIR`, `project_pylib_dir` |
| `core/exec/env_integrity.py` | ABI anchor (`abi_anchor_constraints`), base freeze (`ensure_base_constraints`, `_freeze_pins`), `env_selfcheck`, `gpu_capability_ok`/`torch_cuda_build`, `self_heal_base`/`repair_base`/`base_health`, `verify_python_imports` |
| `core/jobs/slurm_entry.py` | background-job entry; GPU verify-at-use preflight + numpy canary |
| `install/core/inject-accelerator.sh` · `install/linux/setup.sh` | deployment-conditional base: `ABA_ACCELERATOR` (config.env) → CPU vs CUDA torch pin |
| `core/exec/isolated_env.py` | isolated env build/run + per-env lock |
| `content/bio/tools/discovery.py` | agent surface: `ensure_capability`, `propose_capability`, `search_bioconda`/`search_pypi` |
| `backend/kernels/…` (run_python preamble) | assembles the run's `sys.path`: base + project overlay |
| `misc/env_refactor.md` | design/evolution log (§0 as-built); `misc/capabilities.md`, `misc/capdat_impl.md` |

## Known gaps

- **Packages with NO wheel at all.** `--prefer-binary` + the ABI anchor handle the common
  old-toolchain case (a wheel exists for *some* version, or numpy would be rebuilt). But a
  package that ships **only** an sdist for this platform/Python still source-builds and can
  fail on an old cluster GCC. For such a library available prebuilt on **conda-forge**, there
  is no importable route today: conda provisioning targets the CLI tools env (PATH only), so
  a conda-forge *library* isn't importable by `run_python`. (Planned: a conda path that lands
  the lib in an importable layer, pinned to the base numpy ABI.)

- **Heterogeneous cluster: the install node isn't the compute node.** Install/build runs on
  the CPU login node, but the software runs on partition-specific hardware (GPU, big-mem, a
  different CPU microarch). Today we sidestep this for the *solve* — conda artifacts are
  prebuilt and declarative, so `CONDA_OVERRIDE_CUDA` lets the login node *assert* the target's
  capability without being there — and per-job `gpu_capability_ok` verifies at run time. Two
  things genuinely need a target node and are only partially covered:
  - **Install-time verify.** We assert `__cuda` at build; we don't yet *confirm at install*
    that the built CUDA runtime actually initializes on the GPU partition (driver new enough).
    Planned: a post-`create-env` step that `sbatch`es a one-shot `torch.cuda.is_available()`
    probe to each GPU partition class (from `hpc.yaml`) and **fails loud** on a mismatch
    (e.g. built `cuda126` but the driver caps at 12.4 → rebuild with `ABA_CUDA_VERSION=…`),
    instead of deferring the discovery to the first GPU job. (`aba doctor` gets the same probe.)
    NB: such a job must write results to **shared FS**, not node-local `/tmp`.
  - **Build-on-target.** For artifacts that must compile against the node's actual CUDA/driver
    or CPU arch (source-only wheels, `-march=native`, CUDA extensions like flash-attn), the
    login-node build is wrong hardware. Provisioning would dispatch the build *into a job* on
    the target partition (via the existing `slurm_submitter`) and cache the wheel per
    node-class. Not built today; the wheel/pkg cache would key by partition class.
