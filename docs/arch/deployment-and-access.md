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
  never reaches the cluster. (The only `platform.system()` reads in the tree pick a micromamba
  binary for the arch — `core/exec/mamba.py:29` — and stamp a bug-report line — a compute/ABI
  and a diagnostic concern, not business logic.)
- **Config is driven, not hardcoded.** Every mutable-state root and every operational toggle
  resolves **from the environment at use-time** — lazy dirs re-read on each access, per-tier
  overrides, a re-parsed `config.env`. A test harness or a new deployment repoints a tier
  *without editing code*. Prevents import-time-frozen paths (a test poisoning the global
  Jupyter dir) and toggles scattered as literals.
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
                              │   (deployment policy)    │   │ core/runtime/actor.py    │
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

`core/config.py` is the single home for mutable-state roots. `RUNTIME_DIR` is the roof for
*all* runtime state, **hard-separated from the source tree** so `git status` stays clean and
`--reload` doesn't die when an install writes under `envs/` (`core/config.py:12-20`). Every
root is a **`_LazyDir`** (`core/config.py:21`) — a `PathLike` proxy that **re-resolves from
the environment on every use**, so a harness or a runtime swap that sets `ABA_RUNTIME_DIR`
*after* import is honored instead of a value frozen at import (`RUNTIME_DIR`
`core/config.py:85`; `ENVS_DIR` `:99`). Each tier also carries its **own** env override
(`DATA_DIR`, `ABA_ENVS_DIR`, `ABA_PROJECTS_DIR`…) so one tier repoints without moving the
rest (`_resolve_under_runtime`, `core/config.py:78`). Everything a project owns consolidates
under `projects/<pid>/` — one dir to back up, export, or delete atomically
(`project_root`, `core/config.py:336`).

Two whole-system modes ride the same env-driven resolution: **`SINGLE`** — when
`ABA_DB_PATH` is set, the e2e/eval harness owns one DB and the multi-project registry is
bypassed (`core/projects.py:35`, `current()` returns `"single"` `:214`); and **`FAKE`** —
`ABA_FAKE_SESSION` swaps the live LLM for a recorded transcript (`core/config.py:260`,
consumed in `core/llm.py:597`). Neither is a code branch in business logic — both are
config the core reads at its seams.

**Target-conditionals live at the compute seam, and only there.** Exactly two facts differ
by target, both resolved from `config.env`, never from a `if target==…`:

- **`ABA_BATCH_SUBMITTER`** (`local|slurm`) selects the `BatchSubmitter` implementation
  (`core/jobs/submitter.py:48`, `core/exec/modules.py:71`). The routing *policy* — interactive
  vs. background, when a job sbatches — is identical everywhere; only the ABI behind the
  protocol changes. Owned by [`jobs-and-hpc.md`](jobs-and-hpc.md).
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
contextvar can't cross FastMCP's tool-dispatch task boundary (`core/runtime/actor.py:5-13`,
`core/runtime/tool_ctx.py:9-13`; exec-born creates resolve it from the exec's run_id,
`agent_actor_for_exec`). The actor string is *descriptive* provenance — its meaning and use
are owned by [`provenance.md`](provenance.md); here it is the *who* half of the access seam.

**The reserved principal.** `human_actor(uid="local")` (`core/graph/derivation.py:64`)
hardcodes `uid="local"`: single-user today, but the `human:<uid>` shape is the reserved seam
for real identity. Likewise `CAPABILITY_APPROVAL` (`core/config.py:264`)
defaults `"auto"` (solo) with `"ask"` reserved as the multi-user review gate.

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
| `core/config.py` | `_LazyDir` env-live roots (`RUNTIME_DIR`/`ENVS_DIR`/…), per-tier overrides, `project_root`, `SINGLE`/`FAKE`/`CAPABILITY_APPROVAL`, live `config.env` model reparse |
| `core/web/deps.py` | `require_project` — per-request project pin (412 on no-context) + ambient `human:local` |
| `tests/test_project_pinning_coverage.py` | the access-gate CI invariant: every mutating route pinned or justified-exempt |
| `core/runtime/actor.py` · `core/runtime/tool_ctx.py` | ambient actor contextvar; why the agent path attributes explicitly across the MCP boundary |
| `core/graph/derivation.py` | `human_actor(uid="local")` / `agent_actor(run_id)` — the reserved `human:<uid>` seam |
| `core/projects.py` | per-project SQLite registry; `set_current`/`bind` (contextvar DB isolation); `SINGLE` mode |
| `core/bundle/scope_resolver.py` | startup identity/group/site.yaml resolution → the ordered scope chain |
| `core/jobs/submitter.py` · `core/exec/modules.py` | `ABA_BATCH_SUBMITTER` — the compute-config target seam |
| `core/jobs/hpc_config.py` | `hpc.yaml` as optional override; live `sinfo`/`sacctmgr` detection when absent |
| `install/linux/setup.sh` · `install/…/templates/aba.template` | installer `write_cfg` → `config.env`; the launcher sources it into the backend env |
| `install/` (`core`,`mac`,`linux`,`ood`,`sif`) | the install-type shells — **procedures owned by `docs/install/`** |

## Known gaps

- **Real identity / multi-user enforcement is deferred.** `human_actor` hardcodes `"local"`;
  `require_project` pins *which* project but does **not** check that the caller *may* access it
  — there is no authn/authz layer. Single trusted OS user per server process is assumed. The
  principal (`human:<uid>`) is a reserved seam, not a live check.
- **Scope isolation is ambient-DB only.** Cross-project safety rests entirely on separate
  SQLite files + contextvar binding; there is no live `store._scope_of` filter over a shared
  tenant store. A genuinely shared multi-tenant server would need the principal threaded to the
  data layer *and* a scope predicate — neither exists today.
- **`config.env` has no schema validation** beyond `aba doctor`'s targeted checks
  (accelerator-vs-base, submitter-vs-Slurm-presence). A typo'd or stale operational toggle is
  caught only where a specific check exists, not by a general config-lint.
