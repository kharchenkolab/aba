# Running ABA on a Slurm cluster (personal install)

This guide sets up your **own** ABA instance on an HPC cluster you have an account
on, launched by hand, so that long analyses run as **Slurm jobs** on compute nodes
— off your login session, on the resources you ask for, and surviving a restart.

If your cluster offers ABA through **Open OnDemand** instead, use
[cluster_open_ondemand.md](cluster_open_ondemand.md) — that's the managed,
click-to-launch path. This guide is the do-it-yourself alternative.

---

## How it fits together

ABA is a small web server you run as a normal user. It doesn't do heavy compute
itself — it *orchestrates*. When the agent backgrounds a long step, ABA submits a
Slurm job for it.

```
   your laptop ──SSH tunnel──▶  ABA web server          ──sbatch──▶  compute node
   (browser)                    (login node, or an                   (runs the job,
                                 interactive allocation)              writes results to
                                        │                             the shared filesystem)
                                        └──────── reads results ◀─────────────┘
```

Two things make the Slurm path work, and both are about a **shared filesystem**:

1. ABA runs where it can call `sbatch` (a login node, or inside an interactive
   allocation) and where it can see the **same filesystem the compute nodes see**.
2. ABA's runtime directory (its environments + each job's working dir) lives on
   that shared filesystem, so a job running on a compute node can hand its results
   back. ABA detects completion by watching for a sentinel file the job writes —
   no callbacks, no open ports.

> **The one rule:** install ABA (code, environments, runtime) on a filesystem that
> is visible from both your launch node and the compute nodes — your `home`,
> `scratch`, or a project space. Local `/tmp` will not work for Slurm jobs.

---

## Prerequisites

- An account on the cluster and SSH access.
- The **Slurm client** on your launch node — `sbatch`, `squeue`, `sacct`,
  `scancel`. (Login nodes have it; check with `which sbatch`.)
- A **shared filesystem** reachable from the launch node and the compute nodes.
- **conda / mamba** (Miniforge). Many clusters provide it as a module
  (`module load miniforge`); otherwise install Miniforge into your home once.
- An **Anthropic API key** (simplest on a cluster), or a Claude Code OAuth token.

Pick one shared-filesystem directory to hold everything, e.g.:

```bash
export ABA_BASE=/scratch/$USER/aba      # <-- a SHARED path; adjust to your cluster
mkdir -p "$ABA_BASE"
```

---

## 1. Get the code

```bash
cd "$ABA_BASE"
git clone https://github.com/kharchenkolab/aba.git
git clone https://github.com/kharchenkolab/aba-recipe-pack.git
```

## 2. Create the environments

ABA uses two conda environments, both placed under your shared `ABA_BASE` so the
compute nodes can use them:

```bash
# Python runtime (the "venv": Python 3.12 + Node 20 + the bio stack)
mamba env create -p "$ABA_BASE/runtime/.venv" -f aba/install/mac/environment.yml

# R / command-line tools base (R + Seurat/Bioconductor + IRkernel)
mamba env create -p "$ABA_BASE/runtime/envs/tools" -f aba/install/mac/r-environment.yml
```

(The files live under `install/mac/` for historical reasons — they are plain
conda specs and build on Linux.)

## 3. Build the web UI

The Python env includes Node, so use its `npx`:

```bash
cd "$ABA_BASE/aba/frontend"
"$ABA_BASE/runtime/.venv/bin/npm" ci
"$ABA_BASE/runtime/.venv/bin/npx" vite build      # → frontend/dist
cd "$ABA_BASE"
```

## 4. Import the recipe pack

The recipe pack gives the agent curated bioinformatics recipes **and** the
capability catalog (so it provisions packages cleanly instead of guessing). An
install without it works but is noticeably weaker. Make an installation-scope
bundle and point ABA at it:

```bash
mkdir -p "$ABA_BASE/installation/skills/recipes" "$ABA_BASE/installation/catalog"
cp -r aba-recipe-pack/recipes/*        "$ABA_BASE/installation/skills/recipes/"
cp -r aba-recipe-pack/catalog/*        "$ABA_BASE/installation/catalog/"
```

You'll point `ABA_INSTITUTION_BUNDLE` at `$ABA_BASE/installation` in the next step.

## 5. Configure (`.env`)

Create `aba/.env` (the backend reads it on startup):

```bash
cat > "$ABA_BASE/aba/.env" <<EOF
# ── Model + credential ───────────────────────────────────────────────
ABA_MODEL=claude-haiku-4-5-20251001
ABA_LLM_CREDENTIAL=apikey
ANTHROPIC_API_KEY=sk-ant-...                 # your key

# ── Runtime (MUST be on the shared filesystem) ───────────────────────
ABA_RUNTIME_DIR=$ABA_BASE/runtime
ABA_ENVS_DIR=$ABA_BASE/runtime/envs
ABA_TOOLS_DIR=$ABA_BASE/runtime/envs/tools
ABA_FRONTEND_DIST=$ABA_BASE/aba/frontend/dist
ABA_INSTITUTION_BUNDLE=$ABA_BASE/installation

# ── Send background jobs to Slurm ────────────────────────────────────
ABA_BATCH_SUBMITTER=slurm
ABA_HPC_CONFIG=$ABA_BASE/hpc.yaml
EOF
```

To use an OAuth token instead of an API key, set `ABA_LLM_CREDENTIAL=oauth_cc` and
make `~/.claude/.credentials.json` available (or export `CLAUDE_CODE_OAUTH_TOKEN`).

## 6. Describe your cluster's queues (`hpc.yaml`)

ABA maps the agent's resource estimate onto **your** partitions. Create
`$ABA_BASE/hpc.yaml` describing what's available — match your cluster's real
partition names and limits (`sinfo` shows them):

```yaml
hpc:
  partitions:
    # name = your real Slurm partition; limits cap what ABA will request
    - {name: short, max_cores: 16,  max_mem_gb: 64,  max_walltime_h: 4,  gpu: false}
    - {name: long,  max_cores: 64,  max_mem_gb: 512, max_walltime_h: 72, gpu: false}
    - {name: gpu,   max_cores: 16,  max_mem_gb: 128, max_walltime_h: 24, gpu: true}
  qos: [normal]            # optional → passed as --qos=<first>
  account: my_allocation   # optional → passed as --account
  defaults: {partition: short, cores: 1, mem_gb: 4, walltime_h: 4}
```

How ABA uses it: from the agent's estimate (runtime, and optional cores/memory/GPU
hints) it picks the **first partition that fits**, else the largest, and clamps the
request to that partition's ceilings. A job needing GPU goes to a `gpu: true`
partition. If a request needs no specific memory, set `mem_gb: 0` (in defaults or
the estimate) and ABA omits `--mem`, letting the scheduler use its default. ABA
never queries the cluster live — this file is the source of truth.

## 7. Launch

**Where to run it.** ABA itself is light (it orchestrates and submits jobs), so a
**login node** is usually fine. If your cluster discourages long-running processes
on login nodes, start an interactive allocation first and run ABA there:

```bash
salloc --time=8:00:00 --cpus-per-task=2     # adjust to your cluster
```

**Start the server** (from the launch node):

```bash
cd "$ABA_BASE/aba"
set -a; source .env; set +a
cd backend
"$ABA_BASE/runtime/.venv/bin/uvicorn" main:app --host 127.0.0.1 --port 8000
```

**Reach it from your laptop** with an SSH tunnel. If ABA is on a login node:

```bash
ssh -L 8000:localhost:8000 you@cluster.example.edu
# then open http://localhost:8000
```

If ABA is on an allocated compute node `<node>`, tunnel through the login node:

```bash
ssh -L 8000:<node>:8000 you@cluster.example.edu
```

(`squeue -u $USER` or the `salloc` output tells you `<node>`.)

## 8. Verify Slurm offload

In the chat, ask for a backgrounded job, e.g.:

> *"Use run_python with background=True to sleep 60 seconds and print done."*

Then confirm it reached Slurm:

```bash
squeue -u $USER          # you should see a job named  aba-job_xxxx
```

In the UI, open **(i) → Jobs**: the card shows you're in **Slurm** mode, each job
shows its live state / node / resources, and the job moves PENDING → RUNNING →
done. (Plain, short `run_python` calls run interactively in-process and won't
appear in `squeue` — only **backgrounded** work is submitted to Slurm.)

---

## What runs where

| Call | Where it runs |
|---|---|
| `run_python` / `run_r` (default, short) | In-process on the ABA node (interactive kernel) |
| `run_python(background=True)`, or a run estimated as long | **Slurm job** on a compute node |
| `run_python(env='myenv', background=True)` | Slurm job that runs **inside** your isolated env `myenv` (its own python; for R, its lib first on `.libPaths()`) |

Resource hints the agent can pass to size a job: `est_cores`, `est_mem_gb`,
`est_gpu` (plus an estimated runtime → walltime). They're mapped through
`hpc.yaml` as described in §6.

## Durability across restarts

Background **Slurm** jobs survive ABA restarting: the job keeps running on the
cluster, and on restart ABA re-adopts queued/running jobs and picks up any that
finished while it was down (it reads the results from the shared filesystem). A
**completed** job is always visible — it's a saved record. The exception is the
*default in-process* lane: a short interactive run that was mid-flight when ABA
stopped cannot be recovered. So: anything that must outlive your session should be
**backgrounded** (which, here, means Slurm).

## Requirements recap (if Slurm jobs misbehave, check these first)

1. **Shared filesystem** — `ABA_RUNTIME_DIR`, `ABA_ENVS_DIR`, and the cloned repo
   must all be on a path the compute nodes can see. This is the usual culprit:
   *"isolated env … is not available on this node"* or a job stuck `running`
   forever means the node can't see the runtime.
2. **Slurm client on the launch node** — `which sbatch` must succeed where ABA runs.
3. **Compatible compute nodes** — the conda environments are built once and used by
   the nodes over the shared filesystem; this works when the nodes share the OS /
   glibc with the build host (the normal case on a homogeneous cluster).

## Troubleshooting

- **Job rejected: "Memory specification can not be satisfied"** — a partition's
  real memory is below what you requested. Lower `max_mem_gb` in `hpc.yaml`, or set
  `mem_gb: 0` to omit `--mem`.
- **Jobs sit in `PENDING (Resources)`** — the cluster/partition is busy; they'll
  start when nodes free up. Nothing to fix.
- **R job: "Rscript not provisioned"** — run any small R command once
  (interactively) to provision the R base under `ABA_ENVS_DIR`, then retry.
- **Wrong partition/account** — `hpc.yaml` must use your cluster's real partition
  names; `sinfo` and `sacctmgr show assoc user=$USER` show valid values.
