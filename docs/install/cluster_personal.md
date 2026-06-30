# Running ABA on a Slurm cluster (personal install)

This guide sets up your **own** ABA instance on an HPC cluster you have an account
on, launched by hand, so that long analyses run as **Slurm jobs** on compute nodes
— off your login session, on the resources you ask for, and surviving a restart.

If your cluster offers ABA through **Open OnDemand** instead, use
[cluster_open_ondemand.md](cluster_open_ondemand.md) — the managed, click-to-launch
path. This is the do-it-yourself alternative.

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

The Slurm path rests on **one rule** — everything ABA touches must live on a
**shared filesystem** the compute nodes can see:

> Install ABA (its home *and* its runtime) on `home`, `scratch`, or a project space
> that's visible from both your launch node and the compute nodes. Local `/tmp`
> will not work. A job stuck `running` forever, or *"isolated env … not available"*,
> almost always means something landed on a non-shared path.

A job hands results back by writing to that shared filesystem; ABA detects
completion by watching for a sentinel file — no callbacks, no open ports.

## What you'll need

- An account on the cluster with SSH access.
- The **Slurm client** on your launch node — `which sbatch` should succeed.
- A **shared filesystem** reachable from the launch node and the compute nodes.
- `git` and `curl`. **Python is handled for you:** the installer uses a usable
  `python3` (≥3.9 with `venv`) if one is on your `PATH`, otherwise loads a cluster
  `python` **module**, and failing that **bootstraps its own** with micromamba — so
  you don't need to pre-load a python or conda module. Override the choice with
  `ABA_PYTHON=/path/to/python3`.
- An **Anthropic API key** (simplest on a cluster) or a Claude.ai subscription.

## Install

Run the Linux installer with the **`--cluster-personal`** profile, pointing the
runtime at a shared path. Your home directory is usually shared on a cluster, so the
default install location (`~/.aba`) is fine; if your home is *not* shared, also set
`ABA_HOME` to a shared path (see the note).

```bash
git clone git@github.com:kharchenkolab/aba.git && cd aba
./install/linux/setup.sh --cluster-personal \
    --runtime-dir /scratch/$USER/aba \      # a SHARED path — adjust to your cluster
    --api-key sk-ant-…                       # or omit and run `aba auth` afterwards
```

This builds the self-contained environment (Python + R + the bio stack), imports
the recipe library, builds the UI, and configures Slurm offload — it sets
`ABA_BATCH_SUBMITTER=slurm`, puts the runtime on your shared path, and **probes
`sinfo` and `sacctmgr` to write a starting `~/.aba/hpc.yaml`** describing your
partitions, your valid **QOS** (with their walltime caps), and your **account**. It
installs an `aba` launcher under `~/.aba`.

> - **Home not shared?** Add `ABA_HOME=/scratch/$USER/aba-home` before the command so
>   the environment itself is on the shared filesystem too. Package caches
>   (`MAMBA_ROOT_PREFIX`, `CONDA_PKGS_DIRS`, `PIP_CACHE_DIR`) default to under
>   `ABA_HOME` so conda can **hardlink** into the env — keep them on the same
>   filesystem as the env or the build crawls (every package is copied, not linked).
> - **Python via a module?** If no usable `python3` was on your `PATH`, the installer
>   may have used `module load python`; it prints a note when it does. A later
>   `aba update` runs in a venv built from that python, so either load the same module
>   first or re-run the installer with `ABA_PYTHON` pointed at a self-contained python
>   to drop the dependency.
> - **Credentials without a key:** run `aba auth` — it prints a URL you approve in any
>   browser, then paste the code back.

## Tune your cluster's queues (`hpc.yaml`) — optional

**You can usually skip this.** The installer already wrote `~/.aba/hpc.yaml` by
probing the cluster — partitions and their limits from `sinfo`, and your valid
**QOS** + **account** from `sacctmgr` (the things `sinfo` alone can't report, and
which jobs are silently rejected for missing). The runtime router also re-checks
live `sinfo`, so the file is a starting point you only edit to change what was
discovered — prefer a different QOS, tighten a walltime, or fix a name.

A generated `hpc.yaml` looks like:

```yaml
hpc:
  partitions:
    # name = your real Slurm partition; the limits cap what ABA will request
    - {name: c, max_cores: 22, max_mem_gb: 76,   max_walltime_h: 336, gpu: false}
    - {name: m, max_cores: 76, max_mem_gb: 1436, max_walltime_h: 336, gpu: false}
    - {name: g, max_cores: 30, max_mem_gb: 347,  max_walltime_h: 336, gpu: true}
  qos: [long, medium, short, rapid]   # ranked; ABA passes --qos=<first> on EVERY job
  account: my_allocation              # passed as --account (omitted if you have none)
  defaults: {partition: c, cores: 1, mem_gb: 4, walltime_h: 4}
```

**About `qos`** — it's a *ranked* list, and ABA submits `--qos=<qos[0]>` on every
job. The installer ranks your QOS **most-permissive first** (largest `MaxWall`), so
out of the box nothing is rejected for asking too much walltime, and it **clamps each
partition's `max_walltime_h` to that QOS's cap** so the router never over-requests.
The list may include partition-scoped variants (e.g. `c_long`) ranked among the
generic ones. If your jobs are short *and* your cluster gives shorter-walltime QOS
higher scheduling priority, **reorder the list** to put that QOS first.

From the agent's estimate (runtime, and optional cores / memory / GPU hints) ABA
picks the **first partition that fits**, else the largest, and clamps the request to
that partition's ceilings; GPU work goes to a `gpu: true` partition. Set `mem_gb: 0`
to omit `--mem` and let the scheduler use its default. (`sacctmgr show assoc
user=$USER` lists your valid accounts/QOS.)

> Re-running `setup.sh --cluster-personal` **regenerates** `hpc.yaml` from a fresh
> probe, overwriting hand edits — back it up first if you've customized it.

## Launch and reach it

ABA is light, so a **login node** is usually fine. If your cluster discourages
long-running processes there, start an interactive allocation first and launch ABA
inside it (`salloc --time=8:00:00 --cpus-per-task=2`).

```bash
aba up                 # start ABA (uses the config the installer wrote)
aba status             # confirm it's running
```

Reach it from your laptop with an SSH tunnel, then open **http://localhost:8000**:

```bash
ssh -L 8000:localhost:8000 you@cluster.example.edu          # ABA on a login node
ssh -L 8000:<node>:8000   you@cluster.example.edu          # ABA on compute <node>
```

(`squeue -u $USER` or the `salloc` output tells you `<node>`.)

## Verify Slurm offload

In the chat, ask for a backgrounded job:

> *"Use run_python with background=True to sleep 60 seconds and print done."*

Then confirm it reached Slurm with `squeue -u $USER` (you'll see `aba-job_xxxx`). In
the UI, **(i) → Jobs** shows you're in **Slurm** mode and each job's live state /
node / resources. Plain short calls run interactively in-process and won't appear in
`squeue` — only **backgrounded** work is submitted.

## What runs where

| Call | Where it runs |
|---|---|
| `run_python` / `run_r` (default, short) | In-process on the ABA node (interactive kernel) |
| `run_python(background=True)` / a step that needs more than this node has | **Slurm job** on a compute node |
| `run_python(env='myenv', background=True)` | Slurm job **inside** your isolated env `myenv` |

## Durability across restarts

Background **Slurm** jobs survive ABA restarting: the job keeps running on the
cluster, and on restart ABA re-adopts queued/running jobs and picks up any that
finished while it was down. Completed jobs are always visible (a saved record). Only
the *default in-process* lane can't be recovered mid-flight — so anything that must
outlive your session should be **backgrounded** (which, here, means Slurm).

## Troubleshooting

Run **`aba doctor`** first — on a cluster it also checks `sinfo` reachability and
that `ABA_BATCH_SUBMITTER=slurm`. Common issues:

- **Job stuck `running`, or "isolated env … not available"** — something is on a
  non-shared path. Confirm the runtime *and* `~/.aba` are on the shared filesystem.
- **"Memory specification can not be satisfied"** — a partition's real memory is
  below the request. Lower `max_mem_gb` in `hpc.yaml`, or set `mem_gb: 0`.
- **Jobs sit in `PENDING (Resources)`** — the cluster is busy; they'll start when
  nodes free up. Nothing to fix.
- **R job: "Rscript not provisioned"** — run any small R command once interactively
  to build the R base under the runtime, then retry.
- **Job rejected `QOSMaxWallDurationPerJobLimit` / `InvalidQOS`** — ABA submits
  `--qos=<qos[0]>`. Either that QOS's walltime cap is below the request (the installer
  normally ranks the most-permissive QOS first and clamps to it) or the name is stale.
  Check / reorder the `qos` list in `hpc.yaml`; `sacctmgr show assoc user=$USER` lists
  your valid QOS.
- **Wrong partition/account** — `hpc.yaml` must use your cluster's real names. The
  installer fills account + QOS from `sacctmgr`; `sinfo` and `sacctmgr show assoc
  user=$USER` show valid values if you need to correct them.
- **Install crawling?** Package caches live under `ABA_HOME` so conda can hardlink
  into the env. If `ABA_HOME` is on a different mount than the env, every package is
  copied instead and the build is slow — keep both on one filesystem.
