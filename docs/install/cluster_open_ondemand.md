# Deploying ABA on Open OnDemand

A guide for **cluster administrators** standing up ABA as a multi-user Open
OnDemand (OOD) app: one image plus one config, and any number of scientists launch
their own ABA session into their lab's space. (For a single-user install on a
cluster you manage yourself, see [cluster_personal.md](cluster_personal.md).)

The dev harness + end-to-end testing flow lives in `misc/ondemand_runbook.md`; this guide
is the production deployment.

## The model

You build **one image** and deploy **one OOD app**; everything site- and
lab-specific is config and content on shared storage — no per-lab rebuilds.

```
   scientist ─▶ OOD form ─▶ Slurm job on a node ─▶ ABA backend (from the SIF) ─▶ OOD proxy ─▶ browser
                                                        │ reads site.yaml + the lab/user space
```

Content layers into four **scopes**, broadest first — each overrides the last:

| Scope | Lives in | Owner | Change without rebuild? |
|---|---|---|---|
| **system** | the **SIF** (baked recipe pack) | the image build | no — rebuild |
| **institution** | `/cluster/aba/installation` (optional) | platform admin | yes |
| **lab** | `/groups/<lab>/aba/bundle/` | lab admin | yes |
| **user** | `/groups/<lab>/aba/users/<user>/` | the scientist | — |

Environments are **per-user** (`…/users/<user>/envs` — the global + per-project
growth), over a **shared read-only base** (baked into a fat image, or mounted from
shared storage for a slim image). The base is the only env artifact shared across a
lab.

## What you'll customize

Most of a deployment is just these knobs — everything else is generic. The recipe
and policy content comes from the **`aba-recipe-pack`** repo (baked into the image)
plus whatever you add at the institution and lab layers, so you tune the agent's
behavior without touching code.

| What | Where | Set by | Takes effect |
|---|---|---|---|
| **Deployment config** — scope paths, credential chain, image, job offload | `/cluster/aba/site.yaml` (§3) | platform admin | next launch |
| **Site recipes / policy** — institution overlay that *adds to or overrides* the baked pack | `/cluster/aba/installation/` — institution bundle | platform admin | next launch |
| **Slurm queues** (optional — auto-detected from `sinfo`) | `/cluster/aba/hpc.yaml` + `jobs:` in `site.yaml` (§3) | platform admin | next launch |
| **A lab's recipes / rules** | `/groups/<lab>/aba/bundle/` (§5) | lab admin | next launch |
| **Platform code + default recipe pack** (+ the conda/R base in *fat*) — the base every overlay sits on | the SIF (§1) | platform admin | **rebuild** |
| **Launch form** — instance sizes, GPU, group list | `install/ood/aba/form.yml.erb` + `submit.yml.erb` (§4) | platform admin | redeploy app |

The recipe pack ships **baked into the SIF** as the default. The institution bundle
(`/cluster/aba/installation/`) and each lab's bundle (`/groups/<lab>/aba/bundle/`)
**layer over** it — adding or overriding recipes, skills, and rules — and take
effect on the next launch, no rebuild. Rebuild the SIF only to change that *baked
default*, the platform code, or the base packages.

## 1. Build the image

`install/sif/build.sh` builds from the same `install/core` specs as the other
installers, **baking the recipe pack** (and the OOD `aba_preflight.py`, at
`/opt/aba/ood/`) into both profiles:

```bash
export APPTAINER=…/apptainer/bin/apptainer APPTAINER_TMPDIR=…/tmp
export MICROMAMBA=…/bin/micromamba            # fat only — builds the conda + R base
ABA_RECIPES_SRC=/path/to/aba-recipe-pack \
  ./install/sif/build.sh --profile fat        # or slim
```

- **fat** — bakes the conda venv + R/Bioconductor base + backend + frontend +
  recipes. One self-contained artifact (~1.5 GB); nothing to mount.
- **slim** — bakes backend + frontend + recipes (~40 MB); the conda + R base are
  **mounted** from shared storage at run time. Smaller image, env updates without a
  full rebuild — but you stage the base once on the cluster FS.

Both bake CA certs + micromamba so the agent can install packages into the
per-user growth env at run time.

## 2. Place artifacts on shared storage

Reachable by the compute nodes (e.g. under `/cluster/aba`):

```
/cluster/aba/
├── aba.sif                 the image from step 1
├── base/                   slim only: the shared conda + R base to mount
├── site.yaml               the deployment config (step 3)
├── group-skeleton/         copy of install/ood/group-skeleton (lab bootstrap template)
└── installation/           optional institution bundle (site-wide recipes/policy)
```

## 3. Write `site.yaml`

The single file describing your deployment — copy `install/ood/site.yaml.example`
and adjust. The **main admin interject point**: scope paths, the credential chain,
the image, and the auto-create policy.

```yaml
image:
  sif: /cluster/aba/aba.sif
  # base_dir:  /cluster/aba/base          # slim only
jobs:                                     # background-job offload (omit → in-process on the session node)
  submitter: slurm
  hpc_config: /cluster/aba/hpc.yaml
scopes:
  institution: { bundle_path: /cluster/aba/installation }   # optional, over the baked pack
  group:
    enabled: true
    root_path: /groups/{group}/aba
    bundle_subdir: bundle
    auto_create_skeleton: true
    skeleton_template: /cluster/aba/group-skeleton
  user:
    state_dir: /groups/{group}/aba/users/{user}             # per-user runtime (+ /envs)
credentials:
  order: [user_oauth, user_saved, group_shared, user_form_paste]
  user_key_path:  /groups/{group}/aba/users/{user}/.aba/credentials.json   # 0700
  group_key_path: /groups/{group}/aba/.credentials.json                    # optional lab-shared key
```

**Slurm queues** (`jobs.hpc_config`) — **optional**: without it ABA auto-detects
partitions from live `sinfo` (the default partition for CPU jobs, a fitting one for
GPU/large). Configure it only to add an **account / QOS** your cluster requires, or
to override — generate a starting file on a submit node with `python -m
aba_installer.cli hpc-config --out /cluster/aba/hpc.yaml` (schema:
[cluster_personal.md](cluster_personal.md)).

**Credentials:** the `order` chain is tried top to bottom. Drop a lab-shared key at
`group_key_path` (mode 0600) so a whole lab can launch without each user pasting
one; otherwise users paste a key on the form.

## 4. Deploy the OOD app

The app source is `install/ood/aba/` — an OOD *batch_connect* interactive app:

| File | Role |
|---|---|
| `manifest.yml` | the entry under **Interactive Apps → Servers → ABA** |
| `form.yml.erb` | the launch form — reads `/cluster/aba/site.yaml`, lists labs under `/groups`, exposes Lab / Instance / GPU / API-key |
| `submit.yml.erb` | Slurm submission — Instance→cores, optional `--gres=gpu`, 8 h walltime |
| `template/before.sh.erb` | runs on the node *before* the server: picks a port, runs `aba_preflight.py`, sources the generated env |
| `template/script.sh.erb` | launches the backend from the SIF + reverse-proxies the port |
| `view.html.erb` | the **Connect to ABA** button |

**Prerequisite:** Open OnDemand is installed and you can write to the dashboard
host's app dir (`/var/www/ood/apps/sys/`, root).

1. **Name your cluster.** `form.yml.erb` and `submit.yml.erb` reference a cluster
   (`cluster: "dev-cluster"`). Set both to *your* OOD cluster — the one defined in
   `/etc/ood/config/clusters.d/<name>.yml` — or launches won't submit.

2. **Copy it in**, scripts executable:
   ```bash
   rm -rf /var/www/ood/apps/sys/aba
   cp -r install/ood/aba /var/www/ood/apps/sys/aba
   chmod -R a+rX /var/www/ood/apps/sys/aba
   chmod +x /var/www/ood/apps/sys/aba/template/{before,script}.sh.erb
   ```
   *Iterating?* Deploy to `~/ondemand/dev/aba` instead — it shows under **Develop →
   My Sandbox Apps** and reloads each launch (no sys-app copy).

3. **Verify.** OOD discovers sys apps on the next dashboard load (no restart). Open
   **Interactive Apps → Servers → ABA**, pick a lab + instance, launch, and click
   **Connect to ABA** once *Running*. A blocked launch shows why on the session card
   (preflight rc 10 — `status.yaml` `blocked_on` says whether the group isn't
   **enrolled** or its `/aba` is a foreign non-ABA folder).

**Preflight needs no setup.** `before.sh.erb` resolves the image from `site.yaml`
and runs the **baked** `aba_preflight.py` from it (`/opt/aba/ood/aba_preflight.py`,
via `apptainer exec` — `template/preflight.sh`) — version-locked to the backend,
runs on any node, nothing to hand-edit.

## 5. Onboard a lab

How a lab joins depends on `scopes.group.auto_create_skeleton`:

- **`false` — enrollment gate (recommended for a hand-held pilot).** An admin
  enrolls the lab explicitly:
  ```bash
  python /cluster/aba/enroll-group.py <group> [--api-key sk-ant-api… | --oauth-token sk-ant-oat…]
  ```
  That stamps `/groups/<group>/aba` (the `.aba-workspace` marker — and an enrollment
  record), and optionally drops the lab-shared credential (0600). Until a group is
  enrolled it's **hidden from the launch form** and **blocked at preflight** with an
  actionable message (`ui_text.enroll_contact`). Credential is per-group and swappable
  later (API key now, OAuth later) — re-run `enroll-group` to change it.
- **`true`** — a lab's space is auto-created on first launch (no gate).

Either way a same-named **non-ABA** folder is refused (never clobbered). To customize
a lab, the **lab admin** edits the lab bundle — the second interject point:

```
/groups/<lab>/aba/bundle/
├── skills/recipes/<domain>/    lab recipes (layer over the baked pack)
├── catalog/*.yaml              capability-catalog additions
└── rules/                      lab policies / system-prompt addenda
```

Edits here take effect on the next launch — no rebuild.

## What a launch does

The scientist picks their lab + instance size (+ pastes a key if the site requires
one). Slurm schedules the session on a node; `before.sh` runs `aba_preflight`,
which reads `site.yaml`, resolves the **per-user** runtime + envs + credential, and
writes the env block; `script.sh` then runs the backend **from the SIF**
(`apptainer run`), binding `/groups` + `/cluster/aba` + the per-session UI, and OOD
reverse-proxies the browser to it. ABA reads the full scope chain
(system → institution → lab → user). Background jobs offload to Slurm when
`ABA_BATCH_SUBMITTER=slurm` (the `hpc.yaml` schema is the same as
[cluster_personal.md](cluster_personal.md)). Multi-core instances are used in full —
ABA auto-sizes BLAS/OpenMP threads to the Slurm allocation, no config needed.

## Updating

- **Recipes / code / base packages** → rebuild the SIF (step 1) and replace it.
- **Site-wide recipes or policy** without a rebuild → edit `/cluster/aba/installation`.
- **A lab's recipes or policy** → edit that lab's `bundle/`.

## Testing the deployment

For the Dockerized OOD dev harness, the launch round-trip, and host-side SIF
validation (`tests/ood/_sifval.py`, `round_trip.py`), see **`misc/ondemand_runbook.md`** —
the developer runbook.
