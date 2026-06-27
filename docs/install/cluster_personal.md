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
- `git`, `curl`, and Python 3 with `venv` (the installer builds everything else —
  you do **not** need to load a conda module).
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
`sinfo` to write a starting `~/.aba/hpc.yaml`** describing your partitions. It
installs an `aba` launcher under `~/.aba`.

> - **Home not shared?** Add `ABA_HOME=/scratch/$USER/aba-home` before the command so
>   the environment itself is on the shared filesystem too.
> - **No `python3-venv`?** Install it, or pass `ABA_PYTHON=/path/to/python` (e.g. a
>   conda python) before the command.
> - **Credentials without a key:** run `aba auth` — it prints a URL you approve in any
>   browser, then paste the code back.

## Tune your cluster's queues (`hpc.yaml`)

The installer generated `~/.aba/hpc.yaml` from `sinfo`. **Review it** — `sinfo` only
exposes partition sizes, so add your QOS / account and tighten walltime caps:

```yaml
hpc:
  partitions:
    # name = your real Slurm partition; the limits cap what ABA will request
    - {name: short, max_cores: 16,  max_mem_gb: 64,  max_walltime_h: 4,  gpu: false}
    - {name: long,  max_cores: 64,  max_mem_gb: 512, max_walltime_h: 72, gpu: false}
    - {name: gpu,   max_cores: 16,  max_mem_gb: 128, max_walltime_h: 24, gpu: true}
  qos: [normal]            # optional → passed as --qos=<first>
  account: my_allocation   # optional → passed as --account
  defaults: {partition: short, cores: 1, mem_gb: 4, walltime_h: 4}
```

From the agent's estimate (runtime, and optional cores / memory / GPU hints) ABA
picks the **first partition that fits**, else the largest, and clamps the request to
that partition's ceilings; GPU work goes to a `gpu: true` partition. Set `mem_gb: 0`
to omit `--mem` and let the scheduler use its default. (`sacctmgr show assoc
user=$USER` lists your valid accounts/QOS.)

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
- **Wrong partition/account** — `hpc.yaml` must use your cluster's real names;
  `sinfo` and `sacctmgr show assoc user=$USER` show valid values.
