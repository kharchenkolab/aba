# Deployment & access

How the *same* ABA code runs on a laptop, a personal Slurm login node, or a multi-user
Open OnDemand cluster without a per-target branch in business logic — and where identity
and access attach to it.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

Deployment and access are a **cross-cut**: they must attach *uniformly* to every plane
without the planes knowing which target they run on or who is acting. Four imperatives:

- **One codebase, every target.** The business logic that runs on a Mac is *byte-identical*
  to what runs on an OOD cluster. A target difference is only ever a **compute-config or ABI
  fact** — which job submitter, which torch build — resolved at the compute seam, **never a
  branch in business logic.** Prevents the N-codepaths-→-N×-the-bugs trap where a laptop fix
  never reaches the cluster. (The only `platform.system()`/`platform.machine()` reads in the
  tree stamp a bug-report line — `content/bio/tools/feedback.py:215` — and pick the arch's
  micromamba binary in the installer — a compute/ABI and a diagnostic concern, not business logic.)
- **Config is driven, not hardcoded — and declared in one enforced place.** Every mutable-state
  root and every operational toggle is a **typed setting declared once** in `core/config.py`'s
  registry (`setting(...)`), read through a single accessor (`config.settings.<name>.get()`) that
  resolves **from the environment at use-time** — lazy dirs re-read on each access, per-tier
  overrides, a re-parsed `config.env`. A test harness or a new deployment repoints a tier
  *without editing code*. The single read path is a **CI invariant**
  (`tests/test_env_registry_guard.py` fails on any inline `os.environ`/`getenv` read of an
  `ABA_*` var in `backend/` outside `config.py`), so the surface is knowable, not scattered:
  `list_settings()` / `aba settings` render every setting with its value, source, and migration
  tags, and flag any unrecognized `ABA_*` var present in the environment. Prevents
  import-time-frozen paths (a test poisoning the global Jupyter dir) and toggles scattered as
  literals that no `doctor` can see.
- **Access is a cross-cut, not a thread.** Who-may-act and who-*did*-act attach at the
  **boundary** — a per-request project pin + an ambient actor — not sprinkled through business
  logic. **No mutating route is un-gated** — enforced as a CI invariant. Prevents the
  silent-misroute footgun: a request landing in the wrong project's DB.
- **Scope isolation by construction.** A project's data *is* a separate SQLite DB, bound
  per-request/per-turn through a contextvar — not a `WHERE scope=…` clause on a shared table.
  Prevents the cross-project read mid-turn (the 2026-06 turn-history corruption incident).

## The model

Three things compose, all pivoting on a **deployment-agnostic core**:

```
   install-type shells            config topology                deployment-agnostic core
   (write config, build base)     (drives the core)              (the same business logic)
  ┌───────────────────────┐   ┌──────────────────────────┐   ┌──────────────────────────┐
  │ mac / linux /         │   │ .env            (dev)     │   │ core/config.py           │
  │ cluster-personal / OOD│──▶│ config.env  (operational │──▶│  RUNTIME_DIR + lazy tiers│
  │ share install/core    │   │   toggles + creds; admin)│   │ core/web/deps.py         │
  └───────────────────────┘   │ bundle settings.yaml     │   │  require_project (gate)  │
                              │   (deployment policy)    │   │ core/graph/actor.py    │
                              │ hpc.yaml (compute-topo   │   │  current_actor (who)     │
                              │   OVERRIDE, not a toggle)│   │ core/projects.py         │
                              └──────────────────────────┘   │  per-project DB binding  │
                                                             └──────────────────────────┘
```

- **`config.env`** — the installer-written, admin-editable operational layer (mode 0600):
  `ABA_BATCH_SUBMITTER`, `ABA_ACCELERATOR`, `ABA_RUNTIME_DIR`, the credential, cache dirs.
- **The gate** — `require_project` pins the project per-request *and* sets the ambient actor
  to `human:local`. The agent path attributes `agent:<run_id>` instead.
- **The reserved principal** — `human_actor(uid="local")`: `uid` is a hardcoded `"local"`
  today; `human:<uid>` is the shaped-but-unbuilt multi-user seam.

## The deployment-agnostic core (config resolution)

`core/config.py` is the single home for mutable-state roots **and the typed settings registry**
— the one place any `ABA_*` var is read. A setting is **declared once** with `setting(name,
env=…, type=…, default=…, …)` (`core/config.py:178`), which registers it (`_REGISTRY`, `:37`)
and returns a `Setting` accessor (`:88`); callers read the live value via
`config.settings.<name>.get()`, and nothing in `backend/` reads `os.environ` for an `ABA_*`
name directly (the Phase-4 guard enforces it). Each declaration also carries **migration
metadata** — `weft_fate` (what the future weft compute-substrate rewrite does with it) and
`reduction` (the fewer-better-vars plan) — so the surface doubles as a migration ledger.
`list_settings()` (`:233`) renders it all (value + source + tags, secrets redacted) for
`aba settings` / `aba doctor`, and reports any unrecognized `ABA_*` env var as drift. The full
catalogue is generated into [`settings-reference.md`](settings-reference.md).

**Scope of "single source of truth": the backend process config, not every `ABA_*` string.**
The registry + guard own the vars the **backend** reads. `ABA_*` deliberately also appears in
three *other* contracts the guard does **not** police, and shouldn't: the **installer / OOD
launcher shell** (`install/…`, `ABA_PF_*` preflight — a deploy-time contract that *feeds* the
backend; its backend-facing subset is the `deploy_injected` forward-loop), a little **frontend
TS** (build/runtime knobs it reads itself), **bundle `settings.yaml`** (deployment policy the
backend reads via the bundle, not env), and **recipe/tool shell** (`ABA_HOME` etc. inside
executed recipes). Those are separate, intentional surfaces; the registry is authoritative for
the backend server process specifically.

`RUNTIME_DIR` is the roof for *all* runtime state, **hard-separated from the source tree** so
`git status` stays clean and `--reload` doesn't die when an install writes under `envs/`
(`core/config.py:12-20`). Path tiers are `type="path"` settings whose public name stays a
**`_LazyDir`** (`:270`) — a `PathLike` proxy that **re-resolves from the environment on every
use**, so a harness or a runtime swap that sets `ABA_RUNTIME_DIR` *after* import is honored
instead of a value frozen at import (`RUNTIME_DIR` `:346`; `ENVS_DIR` `:372`). Scalar settings
bind their frozen `.get()` value at import (so the ~60 modules importing `KERNEL_ENABLED`/`MODEL`
see no change), while `config.settings.<name>.get()` stays live — the two timings the codebase
relies on, both preserved. Each path tier carries its **own** env override (`DATA_DIR`,
`ABA_ENVS_DIR`, `ABA_PROJECTS_DIR`…) so one tier repoints without moving the rest
(`_resolve_under_runtime`, `:327`). Everything a project owns consolidates under `projects/<pid>/`
— one dir to back up, export, or delete atomically (`project_root`, `:708`).

**Read live vs frozen — the one rule to remember.** Two access forms coexist:
`config.settings.<name>.get()` re-resolves the environment on **every** call (live); a
module-level constant (`from core.config import KERNEL_ENABLED`) is a **frozen import-time
snapshot**. So anything **hot-swappable at runtime must be read via `.get()` (or a live
resolver), never the frozen constant.** Today only the **model** is hot-swapped (Settings →
Model, `set_default_model` rewrites `ABA_MODEL` in `config.env` + `os.environ`): the primary
chat lane already resolves it live via `current_model_for_primary` / `current_model_for_project`,
and the frozen `MODEL` is only the last-resort fallback. Every other `branches=True` toggle
(`KERNEL_ENABLED`, `CAPABILITY_APPROVAL`, `FAKE_SESSION`, …) is a deploy-time decision set
before boot, so a frozen read is correct. New hot-swappable settings must use `.get()`.

Two whole-system modes ride the same env-driven resolution: **`SINGLE`** — when
`ABA_DB_PATH` is set, the e2e/eval harness owns one DB and the multi-project registry is
bypassed (`core/projects.py`, resolved via the settings registry; the former
`ABA_DB_PATH_OVERRIDE` alias was merged into `ABA_DB_PATH`); and **`FAKE`** —
`ABA_FAKE_SESSION` swaps the live LLM for a recorded transcript
(`fake_session` setting, consumed in `core/llm.py`). Neither is a code branch in business
logic — both are config the core reads at its seams.

**Target-conditionals live at the compute seam, and only there.** Exactly two facts differ
by target, both resolved from `config.env`, never from a `if target==…`:

- **`ABA_BATCH_SUBMITTER`** (`local|slurm|worker`) selects the background-job lane
  (`core/jobs/submitter.py`, `core/exec/modules.py`): a local-site weft task, a weft task on the
  declared Slurm-kind site, or the in-process worker fallback. The routing *policy* — interactive
  vs. background, when a job goes to the cluster — is identical everywhere; only the lane behind
  the `BatchSubmitter` protocol changes. Owned by [`jobs-and-hpc.md`](jobs-and-hpc.md).
- **`ABA_ACCELERATOR`** (`cpu|cuda`) selects the base torch build at install. A deployment-
  conditional *ABI* choice, applied by `install/core/inject-accelerator.sh`. Owned by
  [`envs.md`](envs.md).

## Config topology (no floating vars)

A toggle has exactly one home. Four layers, narrowest-wins where they overlap:

- **`.env`** (repo-root, dev only) — auto-loaded at `core/config.py:10`.
- **`config.env`** (`$ABA_HOME/config.env`) — the **operational** layer: installer-written,
  admin-editable, `chmod 600`. The `aba` launcher sources it into the backend's environment
  on boot (`install/…/templates/aba.template:17-22`); the installer's idempotent upsert writes
  it (`write_cfg`, `install/linux/setup.sh:169`). Holds `ABA_BATCH_SUBMITTER`,
  `ABA_ACCELERATOR`, `ABA_RUNTIME_DIR`, the credential, and cache dirs. `ABA_MODEL` here is a
  live-reparsed model default (`_read_aba_model_from_config_env`, `core/config.py:238`) — the
  helper rewrites it on a tray/Control model swap, no restart.
- **Bundle `settings.yaml`** — **deployment policy** from the layered bundle (e.g.
  `default_model`, read at `core/config.py:190`). Owned by
  [`bundle-and-content.md`](bundle-and-content.md).
- **`hpc.yaml`** — **compute-topology detection input / override, not a second home for
  toggles.** It pins partitions / QOS / account when present; when absent, ABA auto-detects
  them live from `sinfo`/`sacctmgr` (`core/jobs/hpc_config.py:1-14`). A `gpu: true` partition
  is *detection input* for placement, never where the accelerator toggle lives (that's
  `config.env`). Owned by [`jobs-and-hpc.md`](jobs-and-hpc.md).

Multi-user deployments add **`site.yaml`** (`$ABA_SITE_CONFIG` or `/cluster/aba/site.yaml`):
path templates and scope-chain layout for a shared cluster, consumed by the scope resolver
below.

## The install-type shells

Four install types — **mac**, **linux**, **cluster-personal**, **OOD** — all share
`install/core`; each differs only in the `config.env` it writes and the base it builds. The
cluster-personal path writes `ABA_BATCH_SUBMITTER=slurm` + auto-detects `ABA_ACCELERATOR`
(`install/linux/setup.sh:210,224`); the OOD path writes a per-session `config.env` at launch
(`install/ood/aba_preflight.py`). **These docs do not duplicate the procedures** — the
how-to for each target is owned by [`docs/install/README.md`](../install/README.md) and its
four per-target guides. What matters here is the invariant they all uphold: an install writes
*config*, never a code fork.

### The launch contract

Turning a resolved environment into `apptainer run` argv happens in exactly one place —
`install/ood/aba/template/aba_launch.sh` — which both consumers *source*:

- the **OOD card** (`script.sh.erb`), the launch users get;
- the **deployment gate** (`aba-vbc/verify.sh`), which decides a release may be promoted.

It assembles the scope binds (`/groups`, `/dev/fuse`, `/dev/shm`, the share root, the
deployment root, the published env store when it sits outside both), `site.yaml binds:` via
`ABA_EXTRA_BINDS`, the slim base remaps (`ABA_BASE_DIR` → `/opt/aba-venv`, `ABA_TOOLS_DIR` →
`/opt/aba-envs/tools`), the session `TMPDIR`, the forwarded env, the Slurm client + munge +
synthesized NSS plumbing, and the host module system. Its input is the env block
`aba_preflight.py` resolved from `site.yaml`; it reads no config of its own and invents no
value. Neither consumer may build argv beside it — the card adds one bind (its per-session
SPA dist), the gate adds none — and `tests/test_launch_contract.py` asserts that by count,
so a hand-rolled bind fails there rather than in production.

The reason it is one file is that two launchers meant to be identical are not, and their
divergence reports success: before this, the gate ran without `ABA_BATCH_SUBMITTER`, so
`submitter_name()` read an unset var under `--containall`, returned `local`, and the
scheduler lane passed having never submitted to Slurm.

The **forwarded** set is not a hand-maintained list: it is the registry's
**`deploy_injected`** surface (`config.deploy_injected_keys()`, = `aba settings
--deploy-env`), mirrored into the contract and drift-guarded by
`tests/test_deploy_forward_loop.py` — add a forwarded var without declaring it `deploy_injected`
(or vice-versa) and CI fails. This closes the "add a var, forget to forward it" desync the
fat-SIF work kept hitting across `script.sh.erb`/`before.sh.erb`/`after.sh.erb`.

`aba-env.sh` itself ends in an unconditional `true`. Its last real line is an optional
`[ -f <group>/.env ] && …` chain, and a sourced file's exit status is its last command's —
so without the terminator the file reports *"did the group carry a .env"* as though it were
*"did the environment load"*, and any consumer running `set -e` dies silently at the moment
it succeeded.

## Shared-artifact layout (`$ABA_SHARE`)

A multi-user / OOD deployment keeps its heavy, read-mostly artifacts on one shared,
node-readable tree — `$ABA_SHARE` (the directory `ABA_SITE_CONFIG`'s `site.yaml` lives in) —
that every session and compute node reads; per-user **state** stays under `$ABA_HOME`. The
tree holds two kinds of thing: **versioned artifacts** (a `releases/<ver>/` dir + an atomic
`current` symlink + a `prev` pointer) and **accumulating data stores** (plain dirs). They
upgrade on **independent cadences** — that decoupling is the point (a recipe fix never
rebuilds an image; a dependency bump never reships the app):

| artifact | shape | upgrades when | mechanism |
|---|---|---|---|
| **app image** | `app/releases/<ver>/aba.sif` + `current` + `prev` | aba code changes | build → drop `releases/<ver>` → atomic flip `current` (`core/release.py`) |
| **compute env images** | one line per stack (`releases/<ver>/image.sqfs` + `current`) | a stack's deps change | weft re-solves → publishes an `image.sqfs` keyed by EnvID (`core/compute/seeding.py` → weft publish/adopt) |
| **recipe/rules bundle** | `installation/` (optionally versioned) | recipes/policy edit | refresh the dir (`aba update`) — no image rebuild |
| **shared refs** | `refs/` (data store) | curator adds data | append; not a release |
| **base env packs** | `installation/envs/*.yaml` | a pin in the shipped pack moves | **nothing automatic** — the installer writes these on a FRESH install and `aba update` never overwrites them; the dir is the operator's. `aba doctor` compares the deployed pins against the ones this ABA ships and prints the difference plus the fix (`cli.env_pack_drift`); applying it stays a deliberate act. Report, never rewrite — an environment is not ours to change under a running deployment, but a deployment that has silently fallen behind is not a state anyone should have to discover from a failure |

**Pin-on-launch.** A session/job resolves `current` → a concrete release **once** at start
(`resolve_current` / `active_release_id`, `core/release.py`; the OOD launcher's
`resolve_release_image`), so flipping `current` never mutates a running job's tree — new work
picks up the new release, in-flight work stays on the one it started with. Rollback = flip
`current` back to `prev`.

**The weft profile (the default).** The controller's own runtime is baked **into the app
image** (a small ~375 MB controller-only SIF); the *only* on-disk envs are the weft-published
compute stacks, mounted read-only on the node via the site's `ro_roots` (the deployment's
published env tree — `ABA_WEFT_PUBLISH_TREE` / `site.yaml` `envs.publish_tree`; consumers
`env_adopt` by name, no solve — the adapter injects the tree into every site's `ro_roots` at
registration). There is no separate controller-env-on-FS here — no `env` component, which is
why the launcher leaves `ABA_BASE_DIR` unset **and why a cluster site under this profile is
`detached`, not shared-fs**: the controller's interpreter lives only inside the image, so a
bare compute node cannot run it. Offloaded jobs therefore ship their code as data and run the
node's own interpreter under a weft-mounted env — they do **not** re-enter the image, and
nothing here needs them to (see `envs.md` §Shared-FS reachability). The
content-addressed `components/` tier itself does still apply: a weft release is
`releases/<ver>/sif -> ../../components/sif/<cid>`, so two releases built from the same image
share one copy of its bytes. `<cid>` must be derived from the artifact's CONTENT —
`stage_release` refuses a single-file component whose id already names different bytes
(`core/release.py`), because `ensure_component` reuses an existing id without looking and a
non-content id therefore serves the previous build under a new release name. The multi-tier
`{sif,env,opt}` composition belongs to the legacy slim model — see `misc/slim_sif_deploy.md`.

**`site.yaml` is not release-specific.** It points at the release *root* (`app/`); the
`current` symlink inside does per-release selection at launch. It changes only when the
*deployment* changes (paths, policy, queue), and sits **above** releases. A **cluster-personal**
install has no `site.yaml` at all — each user's `config.env` points directly at these same
read-only artifacts.

## The access seam (identity, gating, scope)

Access attaches at two boundaries and nowhere else, so business logic never carries an
identity argument.

**The project gate.** `require_project` (`core/web/deps.py:56`) is the canonical per-request
pin: a FastAPI `Depends` that reads `?project_id=` / `X-Project-Id` / the process-global,
sets the active project, and raises **412** when there is no context at all — the exact
symptom of the silent-misroute bug (`_pin_or_412`, `core/web/deps.py:47`). Body-sourced
routes (chat) call the equivalent `_require_project_context(req.project_id)`. This is a **CI
invariant**, not a convention: `tests/test_project_pinning_coverage.py` AST-walks *every*
`@app.{post,patch,delete,put}` and bio-route decorator and **fails** on any mutating handler
that lacks the pin and isn't in a justified `EXEMPT_ENDPOINTS` table (`:53`, `:153`).
Exemptions are limited to genuinely-global endpoints (project lifecycle, server-wide config).
**No un-gated entity mutation** is the enforced property.

**The ambient actor.** The same gate sets the ambient actor to `human:local`
(`require_project` → `set_actor(human_actor())`, `core/web/deps.py:65`). `create_entity`
defaults its `actor` from `current_actor()` when a caller doesn't pass one
(`core/graph/entities.py:109`), so a human HTTP action is attributed for free. The agent path
attributes `agent:<run_id>` **explicitly** rather than via the contextvar, because the
contextvar can't cross FastMCP's tool-dispatch task boundary (`core/graph/actor.py:5-13`,
`core/runtime/tool_ctx.py:9-13`; exec-born creates resolve it from the exec's run_id,
`agent_actor_for_exec`). The actor string is *descriptive* provenance — its meaning and use
are owned by [`provenance.md`](provenance.md); here it is the *who* half of the access seam.

**The reserved principal.** `human_actor(uid="local")` (`core/graph/derivation.py:64`)
hardcodes `uid="local"`: single-user today, but the `human:<uid>` shape is the reserved seam
for real identity. Likewise `CAPABILITY_APPROVAL` (the `capability_approval` setting,
`core/config.py:585`) defaults `"auto"` (solo) with `"ask"` reserved as the multi-user
review gate.

**Scope isolation is ambient-DB.** A project's isolation is *physical*: each project is its
own SQLite under `projects/<pid>/project.db`. `set_current(pid)` repoints `db.DB_PATH`
(`core/projects.py:176`), and `bind(pid)` pins the active DB through a **contextvar** for a
whole turn task so a concurrent request repointing the process-global can't swap the database
out from under a running turn (`core/projects.py:254`, the incident this fixed; the
per-project binding mechanism is owned by [`entity-model.md`](entity-model.md)). A live
tenant filter (`store._scope_of` over a shared multi-user store) is **not** wired: `_scope_of`
is a *promotion* metadata tag (project → broader scope, `core/data/store.py:29`,`:97`), not an
enforced isolation predicate.

**Scope-chain resolution.** `core/bundle/scope_resolver.py` resolves the deployment's
identity facts once at startup — user, group, `site.yaml`, and an **ordered** scope chain
(`resolve_scopes`, `:193`; group via `$ABA_GROUP` / OOD form / unix primary group, `:105`).
It is deliberately scope-count-agnostic: adding a scope appends an entry, no other module
changes shape. The **bundle** scope-chain semantics (system → installation → lab → user →
`EffectiveBundle`) are owned by [`bundle-and-content.md`](bundle-and-content.md); here the
resolver is where per-user identity + group enter, for path placement and the future
credential/access scope.

## Key implementation references

| Where | What |
|---|---|
| `core/config.py` | the **settings registry**: `setting()`/`Setting`/`settings`/`list_settings()`/`deploy_injected_keys()`; `_LazyDir` env-live path tiers (`RUNTIME_DIR`/`ENVS_DIR`/…), per-tier overrides, `project_root`, `FAKE_SESSION`/`capability_approval`, live `config.env` model reparse |
| `tests/test_env_registry_guard.py` | the single-read-path CI invariant: no inline `ABA_*` `os.environ`/`getenv` read in `backend/` outside `config.py` |
| `tests/test_env_registry.py` · `tests/test_deploy_forward_loop.py` | resolved-value snapshot (no behavior drift) + the deploy forward-loop mirrors `deploy_injected` |
| `aba settings [--deploy-env]` (`install/…/cli.py`) | operator view of the full declared surface (value/source/`weft_fate`/`reduction`) + unknown-var drift; or just the launcher-forwarded keys |
| `core/web/deps.py` | `require_project` — per-request project pin (412 on no-context) + ambient `human:local` |
| `tests/test_project_pinning_coverage.py` | the access-gate CI invariant: every mutating route pinned or justified-exempt |
| `core/graph/actor.py` · `core/runtime/tool_ctx.py` | ambient actor contextvar; why the agent path attributes explicitly across the MCP boundary |
| `core/graph/derivation.py` | `human_actor(uid="local")` / `agent_actor(run_id)` — the reserved `human:<uid>` seam |
| `core/projects.py` | per-project SQLite registry; `set_current`/`bind` (contextvar DB isolation); `SINGLE` mode |
| `core/bundle/scope_resolver.py` | startup identity/group/site.yaml resolution → the ordered scope chain |
| `core/jobs/submitter.py` · `core/exec/modules.py` | `ABA_BATCH_SUBMITTER` — the compute-config target seam |
| `core/jobs/hpc_config.py` | `hpc.yaml` as optional override; live `sinfo`/`sacctmgr` detection when absent |
| `install/linux/setup.sh` · `install/…/templates/aba.template` | installer `write_cfg` → `config.env`; the launcher sources it into the backend env |
| `install/` (`core`,`mac`,`linux`,`ood`,`sif`) | the install-type shells — **procedures owned by `docs/install/`** |

## Publishing packs and promoting

`deploy.sh` owns three operations on a target (`--target stage|prod`, `$SHARE` is the
only difference between them):

- `publish-packs --packs <names>` — builds env packs into the **one shared store**
  (`ENV_STORE`; both targets resolve the same `publish_tree`) and re-renders the card's
  version line. The invocation is code, not a comment: it needs `--bind /dev/fuse`
  (weft mounts squashfs), a bound `HOME` (apptainer refuses `--env HOME`, so the solver
  would write to a 64 MB tmpfs), and `PIXI_CACHE_DIR` on node-local storage (rattler's
  cache locking breaks on parallel filesystems). It never copies a built pack between
  trees: a squashfs bakes its own absolute prefix, so a copy activates only where it
  was built.
- `stage [--build]` — builds and stages a release, then **drives it**: the `smoke` lane
  group first (one short session, no scheduler wait), and only if that passes, the
  `critical` group. A build that fails is reported as *STAGED BUT NOT USABLE* rather
  than "staged", because those were two different states with the same announcement,
  and a human was repeatedly the first thing to send a build a prompt.
- `verify [--full] [--verify-installs] [--lanes …]` — boots the staged image as the OOD
  card does and drives it; writes the `.verified` stamp promote reads, recording the
  tier that actually ran. `--verify-installs` (formerly `--install`, renamed because on
  a deployment script that read as "install something") asks the agent for real
  libraries — the path where a missing compiler or header only ever shows up.
- `promote` — copies the tested bytes, flips `current`, writes the target's site config,
  then **drives the target at its own paths** and publishes the card only if it answers.
  On failure it rolls back, restores the previous release's config, and republishes the
  old card. Gated by `scripts/check_pack_pins.py` (every DECLARED pack resolves to a
  published version; rc=2 is not overridable, rc=1 is an operator judgement call).
  It does **not** touch the shared store — promotion is a config diff, and the run
  asserts the store's catalog fingerprint is unchanged.

Why promote drives rather than trusting staging: production is not staging with a
different name — its own share root, card, pins and pack resolution. `do_selfcheck` is
structural and can be entirely green while every session is broken, which is what
happened on 2026-08-27.

The card's version line is a **snapshot** taken when the card is written, so publishing a
pack after a promote leaves it advertising versions that no longer exist; `publish-packs`
re-renders it for that reason.

## Known gaps

- **Real identity / multi-user enforcement is deferred.** `human_actor` hardcodes `"local"`;
  `require_project` pins *which* project but does **not** check that the caller *may* access it
  — there is no authn/authz layer. Single trusted OS user per server process is assumed. The
  principal (`human:<uid>`) is a reserved seam, not a live check.
- **Scope isolation is ambient-DB only.** Cross-project safety rests entirely on separate
  SQLite files + contextvar binding; there is no live `store._scope_of` filter over a shared
  tenant store. A genuinely shared multi-tenant server would need the principal threaded to the
  data layer *and* a scope predicate — neither exists today.
- **Setting VALUES aren't deep-validated, though the surface now is.** The registry gives every
  toggle a declared type, default, and (for some) an `enum`, and `aba settings` / `aba doctor`
  flag any **unrecognized** `ABA_*` var in the environment (typo / stale knob) — the general
  config-lint that didn't exist before. But a *recognized* setting with a semantically-wrong
  value (an enum mismatch passes through advisory-flagged; a path that doesn't exist) is still
  only caught where a specific `doctor` check exists (accelerator-vs-base, submitter-vs-Slurm).
  Enum enforcement is advisory in the mechanical pass to preserve behavior; tightening it to
  hard-reject is a reduction-wave follow-up.
- **The legacy fat SIF is a frozen, read-only target — everything must be baked EAGER.** (The
  default is now the small weft profile above; `fat`/`slim` are legacy.) The modules + lazy-env
  systems default to first-use/deferred install, which cannot work against a read-only image. A
  fat SIF (`install/sif/build.sh --profile fat`) bakes the full python base, the R
  tools env, pagoda3 dist, **and the module manifests** (`/opt/aba/install/core/modules`, else
  the registry is empty), and wires three knobs so the runtime reads the baked artifacts as
  ready instead of re-installing: `ABA_TOOLS_DIR` / `ABA_PAGODA3_DIST` (module readiness probes
  in `core/modules/manager.py` honor these — else they look under `$ABA_RUNTIME_DIR`/`$ABA_HOME`
  and miss the baked copies), `ABA_MODULES_EAGER` (promotes baked `first_use` modules to `on`),
  and a baked `/opt/aba-venv/.aba-base-stage=ready` marker. The boot R-base top-up
  (`lifespan._provision_r_base_bg`) skips when the tools env is a read-only mount. Get any of
  these wrong and the symptom is silent: a first-use install fires against the read-only image
  (a slow network rebuild into the writable runtime dir, or a hard read-only failure).
