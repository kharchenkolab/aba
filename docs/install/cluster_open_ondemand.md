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

## 1. Build the image

`install/sif/build.sh` builds from the same `install/core` specs as the other
installers, **baking the recipe pack** into both profiles:

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

## 4. Deploy the OOD app

The app source is `install/ood/aba/` (an OOD *batch_connect* interactive app:
`form.yml.erb` lists labs from `/groups` and reads `site.yaml`; `before.sh.erb`
runs `aba_preflight.py`; `script.sh.erb` launches the backend from the SIF). Copy
it into the dashboard's sys-apps dir and keep `script.sh.erb` executable:

```bash
rm -rf /var/www/ood/apps/sys/aba
cp -r install/ood/aba /var/www/ood/apps/sys/aba
chmod -R a+rX /var/www/ood/apps/sys/aba && chmod +x /var/www/ood/apps/sys/aba/template/script.sh.erb
```

## 5. Onboard a lab

A lab's space is created automatically on first launch (from `group-skeleton`,
with a safety check that refuses a same-named non-ABA folder). To customize, the
**lab admin** edits the lab bundle — the second interject point:

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
