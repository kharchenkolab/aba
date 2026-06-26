# ABA on Open OnDemand — build & startup runbook

How ABA is packaged and launched on an Open OnDemand (OOD) cluster, and how the
dev box reproduces that chain for testing. Written to be a practical runbook:
follow it top-to-bottom to take everything down, bring OOD back up on current
code, and live-test through the OOD reverse proxy. It is self-contained; the
only things it points at are runnable scripts under `tests/ood/`
(`round_trip.py`, `chat_test.py`, `aba_preflight.py`, `_sifval.py`).

---

## 0. Two image worlds (don't conflate them)

ABA-on-OOD involves **two completely separate "images,"** and most confusion
comes from mixing them up:

| | **The OOD test harness** | **The ABA application image** |
|---|---|---|
| Format | **Docker** (`hmdc/ondemand_development`) | **Apptainer / SIF** (`aba.sif`) |
| What it is | A mock cluster: OOD dashboard + Slurm controller + 2 compute nodes | One self-contained artifact that bakes the ABA backend + conda venv |
| Where | `~/aba/ood-dev/` (cloned, has its own git) | `~/aba/tools/aba.sif` |
| Role | *Simulates* a cluster so we can drive the OOD launch flow | *Is* what a real cluster node executes (`apptainer exec aba.sif …`) |
| Lifetime | Long-lived infra (leave it up) | Rebuilt whenever backend code changes |

**Critical caveat that trips everyone up:** the dev harness compute nodes
(`dev_c1`/`dev_c2`) are Docker containers, and **nested apptainer-in-docker does
not work here**. So in the dev harness the nodes do **not** run the SIF — they
run ABA straight from the **bind-mounted repo** (`/home/pkharchenko/aba` is
mounted into the nodes). Consequence:

- **Live-testing "through OOD" on this box exercises the live repo code** (your
  uncommitted edits included) — no rebuild needed to test code changes through
  the dev OOD flow.
- **The SIF is validated separately, host-side** (`tests/ood/_sifval.py`), since
  no dev node can execute it. On a *real* cluster the node runs the SIF; the
  bind-mount is only a dev shortcut.

So "bring up OOD with the up-to-date ABA" means two things, done independently:
1. **Dev OOD round-trip** — (re)deploy the app to the dashboard and launch a
   session; the node runs current repo code via the bind-mount.
2. **SIF refresh** — re-stage the backend and rebuild `aba.sif` so the
   production artifact also carries current code, then validate it host-side.

---

## 0b. Install procedures (node / SIF / cluster OOD)

Three procedures. **The recipe pack is never baked into the image — it's
installation-scope *content*, imported into a mounted bundle. Each procedure has
an explicit "import the pack" step; skip it and the agent sees only the ~9 system
skills.** Pack repo: `github.com/kharchenkolab/aba-recipe-pack`.

### i) Install ABA on a Linux node (direct / bind-mount style)

1. Clone the repo: `git clone git@github.com:kharchenkolab/aba.git`.
2. **Python runtime** — the conda-prefix "venv":
   `mamba env create -p <runtime>/.venv -f environment.yml` (Python 3.12 + Node
   20 + bio stack: scanpy/scvi/torch).
3. **R/CLI base** — a sibling conda prefix at `<runtime>/envs/tools`: `r-base` +
   `R_CORE_DEPS` + common packages (Seurat, ggplot2, GEOquery, biomaRt, dplyr,
   `r-irkernel`). `ABA_TOOLS_DIR` points here.
4. **Frontend**: `cd frontend && npm ci && npx vite build` → `frontend/dist`.
5. **Config** (`.env` / env): `ABA_MODEL`, `ABA_LLM_CREDENTIAL` (apikey/oauth),
   `ABA_RUNTIME_DIR`, `ABA_ENVS_DIR`, `ABA_TOOLS_DIR`, `ABA_HOME`.
6. **➜ Import the recipe pack:** clone `aba-recipe-pack`; copy
   `recipes/<domain>/` → the installation bundle's `skills/recipes/<domain>/` and
   `catalog/*.yaml` → its `catalog/`; point the installation scope at it
   (`~/.aba/installation`, or `ABA_INSTITUTION_BUNDLE`, or `site.yaml`).
7. Run: `.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port <p>` (or
   `start.sh`).

### ii) Create the ABA SIF (`tools/aba.def` → `aba.sif`)

Bakes the *substrate* only (code + venv + R base + frontend). Do (i) first — the
SIF copies that install dir.

1. **Stage clean**: copy `backend` + `frontend/dist` to a stage dir, strip
   broken + ancestor-cycle symlinks (apptainer's `cp -fLr` chokes on them).
2. **`aba.def`** (`Bootstrap: docker`, `From: debian:12-slim`, no `%post`):
   `%files` bakes `.venv`→`/opt/aba-venv`, staged `backend`→`/opt/aba/backend`,
   `frontend-dist`→`/opt/aba/frontend-dist`, **and** the R env →
   `/opt/aba-envs/tools` (+ `%environment` `ABA_TOOLS_DIR=/opt/aba-envs/tools`).
3. **Build**: `apptainer build --sandbox aba_sandbox/ aba.def` (iterate) →
   `apptainer build --force aba.sif aba_sandbox/` (portable ~1–2.5 GB artifact).
4. **Validate host-side**: `tests/ood/_sifval.py` (binds mock dirs, runs the SIF,
   checks health/bundle/chat/`run_python`).
5. **➜ Recipe pack: NOT here.** The pack is *not* baked — it's mounted at runtime
   (step iii.2). Baking it would re-couple content to the image.

### iii) Install ABA on OOD on a real cluster

Combines the SIF (image) + mounted content + per-site config + the OOD app.

1. Place `aba.sif` on shared storage reachable by compute nodes (e.g.
   `/cluster/aba/aba.sif`).
2. **➜ Import the recipe pack:** put the installation bundle (pack
   `recipes/`→`skills/recipes/`, `catalog/`, + site policy) on shared storage at
   the institution-scope path (e.g. `/cluster/aba/installation`). One-time
   content deploy, refreshed independently of the SIF.
3. Write `site.yaml` (`/cluster/aba/site.yaml`): scope paths, credential chain,
   group-bundle convention, auto-create policy.
4. Deploy the OOD app: copy `ood-apps/aba` → the dashboard's
   `/var/www/ood/apps/sys/aba`; keep `script.sh.erb` executable.
5. On launch, `script.sh.erb` runs `apptainer exec <binds/env> aba.sif python -m
   uvicorn …` — binding `/groups` (lab shares), `/cluster/aba` (site config +
   installation bundle), and writable runtime/envs; setting `ABA_SITE_CONFIG`,
   `ABA_TOOLS_DIR=/opt/aba-envs/tools`, `ABA_HOME`. The preflight resolves scopes
   + credentials from `site.yaml`; lab bundles at `/groups/<lab>/aba/.bundle`
   layer on top.

**Recipe-pack summary:** always installed as installation- (or lab-) scope
content — `recipes/<domain>/` → bundle `skills/recipes/<domain>/` + `catalog/`.
Node install → a local bundle dir; cluster OOD → shared storage under the
institution scope. Never in the SIF/image.

## 1. Take everything down

```bash
# loose ABA backends (dev servers, test backends)
pkill -f 'uvicorn main:app' ; sleep 2 ; ss -ltnp | grep -E ':8000|:8137' || echo "ports free"

# any OOD-launched session backends on the nodes + queued jobs
sg docker -c 'docker exec dev_slurmctld scancel --user=ood' 2>/dev/null
for n in dev_c1 dev_c2; do
  sg docker -c "docker exec $n bash -lc 'pkill -f uvicorn || true'" 2>/dev/null
done
```

This leaves the Docker harness itself (dashboard + Slurm + nodes) up — that's
infra. Only the ABA *instances* go down. To stop the harness entirely:

```bash
sg docker -c 'cd ~/aba/ood-dev && env SID_SLURM_IMAGE=hmdc/sid-slurm:v3-slurm-21-08-6-1 \
  SID_OOD_IMAGE=hmdc/sid-ood:ood-3.1.7.el8 OOD_UID=$(id -u) OOD_GID=$(id -g) \
  docker compose down -v'
```

**Note:** the box normally also runs a persistent dev backend on `:8000`
(`uvicorn … --reload`) — your main dev server. `pkill -f 'uvicorn main:app'`
takes it down too. Bring it back with `dev/bounce_backend.sh` (or
`start.sh`) when you want it.

---

## 2. The OOD dev harness (Docker)

Cloned outside the repo at `~/aba/ood-dev` (it's the upstream
`hmdc/ondemand_development` stack with its own git — not part of this repo).
First-time bring-up:

```bash
git clone https://github.com/hmdc/ondemand_development ~/aba/ood-dev
cd ~/aba/ood-dev && git submodule update --init --recursive   # --depth1 leaves the submodule empty
sg docker -c 'make ood_build'                                  # builds the Rails dashboard
sg docker -c 'env SID_SLURM_IMAGE=hmdc/sid-slurm:v3-slurm-21-08-6-1 \
  SID_OOD_IMAGE=hmdc/sid-ood:ood-3.1.7.el8 OOD_UID=$(id -u) OOD_GID=$(id -g) \
  docker compose up --build -d'
chmod -R 777 ~/aba/ood-dev/data    # PUN runs as uid 3210 and must write ./data, else the dashboard 500s
curl -k -u ood:ood https://localhost:33000/pun/sys/dashboard -o /dev/null -w '%{http_code}\n'  # warm passenger
```

Once built it stays up across reboots-of-work; you normally just leave it
running. Containers (all currently up):

| Container | Role |
|---|---|
| `dev_ood` | OOD dashboard / PUN (Passenger). HTTPS on host **:33000** |
| `dev_slurmctld` | Slurm controller (`scancel`, `squeue` run here) |
| `dev_slurmdbd`, `dev_mysql` | Slurm accounting DB |
| `dev_c1`, `dev_c2` | Slurm compute nodes — **where the ABA backend actually runs** (1 CPU each) |
| `dev_rt`, `dev_smtp` | Request Tracker / maildev (unrelated to ABA) |

**Bind-mounts (from `~/aba/ood-dev/docker-compose.yml`)** — these are what make
the live repo + mock cluster filesystem visible inside the containers:

| Host path | Container path | Mounted into |
|---|---|---|
| `/home/pkharchenko/aba` | same | `dev_c1`, `dev_c2` (so the node sees the repo + venv + runtime) |
| `~/aba/ood-groups` | `/groups` | `dev_ood`, `dev_c1`, `dev_c2` (mock lab group shares) |
| `~/aba/ood-cluster` | `/cluster/aba` | `dev_ood`, `dev_c1`, `dev_c2` (site.yaml + installation bundle + skeleton) |

So: the **dashboard** sees `/groups` + `/cluster/aba` (enough to render the
form + read site.yaml); the **nodes** additionally see the whole repo, which is
how they run ABA from `…/aba_runtime/.venv/bin/python` against `…/aba/backend`.

Login **ood / ood** (HTTP basic). The dev cluster runs everything as the single
user `ood` (uid 3210).

---

## 3. The ABA OOD app (`tests/ood/ood-apps/aba`)

An OOD *batch_connect* interactive app, deployed as sys app **`aba`**. Files:

| File | Purpose |
|---|---|
| `manifest.yml` | Name/category for the Interactive Apps menu |
| `form.yml.erb` | Launch form. ERB runs at render time: reads `/cluster/aba/site.yaml`, lists lab groups under `/groups`, marks which have an ABA bundle, exposes Lab / Instance / GPU / API-key fields |
| `submit.yml.erb` | `basic` web-app template; maps Instance→cores (`-n 1` / `-n 10`), optional `--gres=gpu:1`, 8 h walltime |
| `template/before.sh.erb` | Runs on the node **before** the server: picks a free port, runs `aba_preflight.py`, sources the generated `aba-env.sh` |
| `template/script.sh.erb` | The main launch script: sets env, builds the per-session frontend, starts uvicorn with a clean-shutdown trap |
| `view.html.erb` | Renders the "Connect to ABA" button once Running |

### Launch sequence (what happens on "Launch")

```
form.yml.erb  ──(render)──▶  user picks Lab/Instance/key  ──▶  submit.yml.erb (Slurm -n cores)
        │
        ▼  Slurm schedules the job on c1 or c2
before.sh.erb ──▶ port=find_port(host)
        │         set ABA_PF_* ; run aba_preflight.py  ──▶ writes aba-env.sh + status.yaml
        │         source aba-env.sh  (ABA_RUNTIME_DIR, ABA_ENVS_DIR, creds, ABA_SITE_CONFIG)
        ▼
script.sh.erb ──▶ HOME=$ABA_RUNTIME_DIR/.home
        │         ABA_TOOLS_DIR=$VENV/../envs/tools   (image-resident R base; no per-group R rebuild)
        │         per-session frontend: cp dist; sed __OOD_PREFIX__ → rnode/$host/$port; ABA_FRONTEND_DIST
        │         cd backend; uvicorn main:app --host 0.0.0.0 --port $port  (run as child + killtree trap)
        ▼
OOD reverse-proxies the browser to  /rnode/$host/$port/  ──▶ ABA UI + /api
```

### `aba_preflight.py` (the site→env bridge)

Runs on the node, reads `/cluster/aba/site.yaml`, and:
- resolves the **scope dirs** (group/user/installation), auto-creating the
  group workspace from the skeleton if absent (with a safety check: refuses a
  same-named folder lacking an ABA marker → exit 10 → `before.sh` aborts);
- resolves **credentials** by the site's `order` chain (saved key → group key →
  pasted key → oauth);
- writes **`aba-env.sh`** (the env block `script.sh` sources) and
  **`status.yaml`** (the session card);
- sets `ABA_SITE_CONFIG` so ABA's *own* `scope_resolver` reads the **same**
  site.yaml for the bundle scope chain (system → installation → group → user).

### `site.yaml` (`~/aba/ood-cluster/site.yaml` → `/cluster/aba/site.yaml`)

The single file describing this deployment. Key bits for the dev stack:
- `scopes.institution.bundle_path: /cluster/aba/installation` — the installation
  bundle (imported recipe pack + site policy).
- `scopes.group.root_path: /groups/{group}/aba`, `bundle_subdir: .bundle`,
  `auto_create_skeleton: true`, `skeleton_template: /cluster/aba/group-skeleton`.
- `scopes.user.state_dir: /groups/{group}/aba/{user}` — projects/db persist here.
- `credentials.order: [user_saved, group_shared, user_form_paste]`.

---

## 3b. Background jobs — in-process vs Slurm

An OOD session **is** a Slurm job on a compute node, holding the cores the launch
form allocated (Light = 1, Heavy = 10). When the agent backgrounds a long run
(`run_python(background=True)`, or a run it estimates as long), ABA can place that
work two ways, selected by **`ABA_BATCH_SUBMITTER`** (exported in
`template/script.sh.erb`):

| `ABA_BATCH_SUBMITTER` | Where the background job runs | Notes |
|---|---|---|
| `local` *(default)* | In-process on the **same** allocated node, using its cores | Simplest. The job ends if the session ends. Good when Heavy already gives enough cores. |
| `slurm` | ABA `sbatch`'s each background job as a **separate** Slurm job | The job gets its own partition/cores/walltime and **survives** the session ending or restarting. Needs nested submission (the session job submits further jobs — allowed on most clusters) and a shared-filesystem runtime dir (used for completion signaling, already true under OOD). |

To enable Slurm offload, the app's `template/script.sh.erb` exports:

```bash
export ABA_BATCH_SUBMITTER=slurm
export ABA_HPC_CONFIG=/cluster/aba/hpc.yaml   # partitions / QoS / defaults (or bundle `hpc:` settings)
```

The agent's estimate (`est_cores` / `est_mem_gb` / `est_gpu`, plus an estimated
runtime) is mapped against the partitions in `hpc.yaml`. **The `hpc.yaml` schema
and the resource model are identical to the personal install** — see
[cluster_personal.md](cluster_personal.md). Submitted jobs, their live Slurm
state, and the session's own allocation appear in the **(i) → Jobs** tab.

## 4. Deploy the app + live-test through OOD

### Deploy / redeploy the app to the dashboard

The app dir is copied into the dashboard container. `docker cp` into an existing
dir merges stale files, so **`rm -rf` first**, then cp, then fix perms (the
`script.sh.erb` **must stay executable** or the job dies instantly):

```bash
sg docker -c 'docker exec dev_ood rm -rf /var/www/ood/apps/sys/aba && \
  docker cp ~/aba/aba/tests/ood/ood-apps/aba dev_ood:/var/www/ood/apps/sys/aba && \
  docker exec dev_ood chmod -R a+rX /var/www/ood/apps/sys/aba && \
  docker exec dev_ood chmod +x /var/www/ood/apps/sys/aba/template/script.sh.erb'
```

### Launch + verify headless (the round-trip)

```bash
cd ~/aba/aba/tests/ood
~/aba/aba/.venv/bin/python round_trip.py aba "Connect to ABA" "ABA"
# PASS = launched → Slurm job → Running → proxy Connect → app page showed marker → deleted
```

### Live-test scenarios through the proxy

The pattern (`chat_test.py`): launch via the dashboard, scrape the
`/rnode/$host/$port/` prefix from the Connect link, then hit the backend
**through the OOD reverse proxy** with basic auth:

```
base = https://localhost:33000/rnode/$host/$port
GET  {base}/api/health
POST {base}/api/projects   {"name": "..."}
POST {base}/api/chat       {"text": "...", "project_id": "..."}
```

This is the real "through OOD" path: the request traverses the dashboard proxy
to the uvicorn the Slurm job started on the node.

### Inter-run hygiene (from README gotchas)

- Each node has **1 CPU**; OOD "Delete" doesn't always `scancel`. Between runs:
  `sg docker -c 'docker exec dev_slurmctld scancel --user=ood'` or the next
  launch hangs in `PENDING (Resources)`.
- Leftover RUNNING jobs fill both nodes — check `squeue` in `dev_slurmctld`.

---

## 5. The SIF (production application image)

The self-contained artifact a real cluster node runs. v1 bakes the working conda
venv + backend + prefix-built frontend dist (a slim image + mounted shared conda
env is the documented follow-up).

- **Toolchain:** rootless apptainer bootstrapped via micromamba at
  `~/aba/tools/apptainer-env` (this box has no system apptainer). Put
  `~/aba/tools/apptainer-env/bin` on `PATH` or apptainer extracts the whole
  image every run.
- **Definition:** `~/aba/tools/aba.def` (Bootstrap: docker, debian:12-slim, no
  `%post` so it builds rootless). `%files` bakes:
  `aba_runtime/.venv → /opt/aba-venv`, staged `backend → /opt/aba/backend`,
  staged `frontend-dist → /opt/aba/frontend-dist`.
- **Staging:** the repo has dangling/cyclic symlinks apptainer's `cp -fLr`
  chokes on, so backend + dist are pre-staged clean into `~/aba/tools/stage/`
  (copy, then delete broken + ancestor-cycle symlinks) before the build.
- **Build:**
  ```bash
  export PATH=~/aba/tools/apptainer-env/bin:$PATH
  export APPTAINER=~/aba/tools/apptainer-env/bin/apptainer
  export APPTAINER_TMPDIR=~/aba/tools/apptainer-tmp
  $APPTAINER build --sandbox ~/aba/tools/aba_sandbox/ ~/aba/tools/aba.def   # iterate
  $APPTAINER build --force   ~/aba/tools/aba.sif      ~/aba/tools/aba_sandbox/  # artifact (~1 GB)
  ```
- **R/CLI base must also be baked** at `/opt/aba-envs/tools` and `ABA_TOOLS_DIR`
  pointed there, or R rebuilds per lab. (TODO: `aba.def` does not yet bake the R
  env — see §7.)
- **Validate host-side:** `tests/ood/_sifval.py` (binds mock `/groups` +
  `/cluster/aba`, runs the image, checks health + bundle scope + chat +
  run_python).

---

## 6. Quick reference — paths

| Thing | Path |
|---|---|
| Repo (git root) | `/home/pkharchenko/aba/aba` |
| Runtime (`.env`, `.venv`, `envs/`) | `/home/pkharchenko/aba/aba_runtime` |
| R/CLI tools env (image R base) | `…/aba_runtime/envs/tools` (`ABA_TOOLS_DIR`) |
| OOD harness (Docker, own git) | `/home/pkharchenko/aba/ood-dev` |
| SIF build area | `/home/pkharchenko/aba/tools` |
| Mock group shares (`/groups`) | `/home/pkharchenko/aba/ood-groups` |
| Mock cluster cfg (`/cluster/aba`) | `/home/pkharchenko/aba/ood-cluster` |
| OOD app source | `…/aba/tests/ood/ood-apps/aba` |
| OOD app deployed | `dev_ood:/var/www/ood/apps/sys/aba` |
| Dashboard URL | `https://localhost:33000` (ood/ood) |

---

## 7. Compute threads & node resource limits

A Slurm node is a *slice* of a big machine: `SLURM_CPUS_ON_NODE` may be 1 while
`/proc/cpuinfo` shows 56 and the cpuset isn't bound (`Cpus_allowed_list: 0-55`).
Left alone, OpenBLAS/OpenMP size their thread pools to the **host core count**
and spawn ~56 threads per kernel. Against the per-user process ceiling
(`RLIMIT_NPROC`, often 4096 and counted across *every* process the user owns on
the whole node) that pegs `pthread_create` to `EAGAIN` and the kernel — or the
whole backend, via libzmq's `abort()` — dies with "Resource temporarily
unavailable" / "can't start new thread".

ABA handles this in `core/exec/cpu.py` (`pin_blas_threads()` at backend startup +
`default_thread_cap()` for kernels): it sizes BLAS/OMP thread pools to the **CPU
allocation**, not the host:

- **Explicit allocation** (`SLURM_CPUS_PER_TASK`/`SLURM_CPUS_ON_NODE`, a cgroup
  CPU quota, or `ABA_CPU_LIMIT`) is **honored in full** — a Heavy node allocated
  16 cores gets 16 BLAS threads, never more than the affinity mask permits.
- **No allocation signal** (an unscheduled box / bare SIF on a fat host) falls
  back to `min(cpu, 8)` so small bio matrices don't oversubscribe.
- `ABA_KERNEL_THREADS` overrides everything.

The vars are set process-wide with `setdefault`, so every child the backend
spawns (kernels, `IRkernel::installspec`, micromamba, `Rscript`) inherits the cap
— and a launch-script/operator value still wins. No OOD `script.sh.erb` change is
needed; the backend reads the Slurm env it already inherits. For a Heavy
(multi-core) instance this is what makes the extra cores actually get used.

**Dev-harness gotcha — sshd zombies exhaust `RLIMIT_NPROC`.** The mock
`dev_slurmctld` container spawns sshd children (its `with-ssh.sh` entrypoint) and
has no init reaper, so `[sshd] <defunct>` zombies accumulate (~3–4/min) and count
against uid 3210's process budget. Left for a day they can consume ~4000 of 4096
slots — every ABA kernel launch then fails on `pthread EAGAIN` no matter how few
threads it asks for. This is a *harness* artifact, not a production issue. Clear
it by restarting the controller (reaps its zombie children):

```bash
sg docker -c 'docker restart dev_slurmctld'
# verify: uid-3210 thread count drops back to ~150
ps -eL -o uid= | grep -c '^ *3210'
# nodes may go down briefly — resume:
sg docker -c 'docker exec dev_slurmctld scontrol update nodename=c1 state=resume'
```

## 8. Validated through OOD (live)

Launched via the dashboard, driven through the `/rnode/<host>/<port>/` proxy,
OAuth credential, 1-CPU node with the BLAS-thread cap (`OPENBLAS_NUM_THREADS=1`):

| Scenario | Result |
|---|---|
| sanity / py / R / catalog | ✓ chat, `run_python`, `run_r` (image R base), `list_capabilities`+`ensure_capability(DESeq2)` |
| scanpy pbmc3k pipeline | ✓ QC→norm→PCA→neighbors→leiden → 11 clusters / 2643 cells (84 s) |
| Seurat v5 workflow | ✓ norm→HVG→scale→PCA→UMAP→cluster → 10 clusters + figure (85 s) |
| scVI provisioning | ✓ scvi-tools imported — but via a circuitous path (see friction below) |

OAuth + auto-renew, the catalog projection (Fix 1), and the R/Python stacks all
work on the constrained node. Seurat (the worst case for the thread explosion)
runs clean on a single BLAS thread.

## 9. Known gaps / TODO / friction

- **Recipe pack / catalog not imported → extra provisioning round-trips.** A lib
  that's present in the base venv but absent from the seeded catalog (e.g.
  `scvi-tools`) makes the agent run `ensure_capability`→`propose`→`ensure` cycles
  that error before it falls back to a direct `import` (which works). Observed:
  `propose_capability → already_available` yet `ensure_capability → error`. Two
  fixes: (a) import the recipe pack into the installation scope so the catalog
  knows these libs (the real fix — also populates recipes); (b) make
  `ensure_capability` short-circuit to success when the library already imports.
- `aba.def` does not yet bake the R/CLI tools env or set `ABA_TOOLS_DIR` — the
  current `aba.sif` (built before that change) is stale on both counts. Refresh
  needed before the SIF is a faithful production artifact.
- Recipe pack not imported on this box → only system skills present, so the
  agent hand-rolls bio workflows instead of following a recipe. The fix is to
  drop the recipe pack into the installation scope
  (`/cluster/aba/installation/skills/recipes/<domain>/` + `catalog/`); the
  bundle scope chain then projects both recipes and the capability catalog at
  once (same mechanism as system skills).

<!-- Updated as the bring-up is actually executed; sections above reflect what
     has been verified by running the chain, not just reading the files. -->
