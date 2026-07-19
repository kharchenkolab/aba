# Compute sites — connecting external compute

> Status: current as of 2026-07. Ships the P0/P1 surface of
> [`misc/compute_settings.md`](../../misc/compute_settings.md) v2 (the design source). Covers
> registering/managing compute sites and the science-first **Settings → Compute** tab.

## What this is for

aba runs analyses on more than the local machine: a lab workstation, the group's Slurm
cluster, later cloud. **Sites** are those machines. This doc is the map for the surface that
connects and manages them — a curated projection over the compute substrate, aimed at
**natural scientists, not sysadmins**: the machine describes itself, aba proposes a complete
configuration, the user confirms or tweaks. The expert surface (every knob) is
[weft-ui](https://github.com/kharchenkolab/weft-ui), reachable via "Advanced ↗".

## The invariants that shape it

1. **One doorway to weft.** All site operations go through `WeftAdapter` behind the
   `SitePort` Protocol (`core/compute/ports.py`, `adapter.py`). `import weft` is confined to
   `core/compute/` by an AST guard (`tests/test_compute_ports.py`); every port method must be
   a real same-named weft tool (drift guard). The Settings tab and the Guide tools reach weft
   **only** through the `/api/compute` router or the ports — never around them.
2. **weft's sqlite is canonical; `weft-sites.yaml` is the declarative bootstrap.** Sites live
   in `<workspace>/.weft/state.db` once registered. The YAML (`sites_config.py`) is what an
   install / the tab writes so a site survives a fresh clone or OOD redeploy, and is the home
   of the **aba-side keys weft has no schema for**: `contract`, `use_for`, long-term
   `storage`. The adapter re-registers any YAML-declared-but-unregistered site at
   `configure()`. Writes are merge-by-name and atomic (tmp + `os.replace`).
3. **aba proposes; weft measures; the user confirms.** The machine-type + configuration guess
   (`inference.py`) is a **pure function over weft's capability record** — no weft import, no
   I/O — so the router, the tab, and the Guide's tools share one judgment. It is aba domain
   policy ("reveal, don't require"), not a weft concern.
4. **aba never handles the user's password.** SSH key setup (`preflight.py`) generates a
   dedicated keypair and hands the user an `ssh-copy-id` line for their *own* terminal; there
   is no password parameter anywhere in the API. Host keys are trust-on-first-use with
   **explicit** consent — the real fingerprint is shown, accepted keys go to aba's own
   known-hosts store under `$ABA_HOME`, never the user's `~/.ssh/known_hosts`.
5. **Connect fast; verify deep in the background.** Registration completes on the fast
   login-node probe; `site_probe_deep` (a real test job per queue, which can wait on a busy
   cluster) runs as a background task and upgrades the card via a `compute` notification —
   it never blocks the connect.
6. **Shared deployments can be read-only.** `ABA_COMPUTE_SELF_SERVICE=false` (the
   `compute_self_service` registry setting, `deploy_injected` — set it in a personal install's
   `config.env`, or via `site.yaml` `compute: {self_service: false}` on OOD/SIF) makes the
   deployment manage its own machines: the tab renders every declared site read-only (no Add,
   no edit/disconnect/free-up), the eight `/api/compute` management endpoints return a 403
   `self_service_disabled`, and the Guide's `probe_compute_site`/`connect_compute_site` tools
   refuse the same way (`sites_config.self_service()` gates router + tools identically, failing
   OPEN so a config hiccup never locks the UI). Reads (status, list, load, verify, reprobe)
   stay available.

## The pieces (where the code is)

**Backend — `core/compute/`**
- `ports.py` — `SitePort` adds `site_probe`, `site_unregister`, `gc_plan`, `gc_sweep` for
  this surface (Test connection, Disconnect, Free up).
- `sites_config.py` — read/merge/atomic-write `weft-sites.yaml`; `aba_keys(name)` reads the
  per-site `{contract, use_for, storage}` block.
- `inference.py` — `propose(caps, …)` (the §5.4 table: kind, name, working root from
  `storage.candidates`, long-term stores, partition preselect, contract, account) and
  `build_site_config(proposal, …)` (proposal → weft `register_site` config). Pure.
- `preflight.py` — ssh reachability `classify` (ok/auth/hostkey/dns/network), `keysetup`
  (no-password), host-key TOFU (`scan_hostkey`/`accept_hostkey`), `remote_facts` (shared-fs
  canary + scheduler + accounts), `canary_paths` (deployment paths proving shared storage).

**Backend — the router `core/web/routers/compute.py`** (registered in `main.py` like
`modules.py`). Endpoints: `status`, `hosts`, `templates`, `sites` (list/detail/load/
footprint), the connect flow (`preflight` → `hostkey`/`keysetup` → `probe` [`register_site`
probe_only + `propose`] → `sites` [register + YAML write + background verify]), and manage
(`verify`, `reprobe`, `PATCH`, `DELETE`, `gc`, `advanced`). `wire_event_relay()` subscribes
to weft's event feed and rebroadcasts `bootstrap.step`/`site.*` onto the notification bus as
`compute` events (wire contract in `core/runtime/wire.py`; `App.tsx` dispatches `aba:compute`).

**Frontend** — `components/ComputeTab.tsx` (status cards with `capsLine` cluster totals +
start estimate + causal health, manage detail with storage roles / Free up / Disconnect),
`components/ConnectMachine.tsx` (the entry → access → probe → proposal → connect flow),
typed client in `lib/api.ts` (`computeApi`). Live refresh on the `aba:compute` window event.

**The Guide as a second front-end** — `content/bio/mcp_servers/aba_core/tools/compute_sites.py`
(`list_compute_sites`, `probe_compute_site`, `connect_compute_site`) drives the same
`inference`/`preflight`/`sites_config` pieces. The tools enforce the disciplines structurally:
no password parameter, explicit `accept_hostkey`, and `connect` refuses without `confirmed=True`.

**weft-ui integration** — `core/web/weftui.py` mounts weft-ui at `/weft` in **shared-controller
mode** (`weft_ui.embed.attach(controller=…)` — a weft-ui change that lets it serve aba's
existing `Weft` instead of constructing a second one on the same workspace). `advanced_url()`
builds the deep link (`?token=…&hide=chat#/compute[/<site>]`); the tab's "Advanced ↗" buttons
open it. No-op when weft-ui isn't installed.

## Placement (the doctrine)

`use_for` (interactive/background/gpu — all default-on; remote interactive is the point) and
`contract` (shared-fs vs detached) are the aba-side keys the lifecycle pick and submit path
read. `weft plans, the agent decides`: the tab sets these in plain terms; the agent reads them
+ weft's `site_load`/plan to choose *where* background work runs, and run records say so in the
tab's own language. See [`jobs-and-hpc.md`](jobs-and-hpc.md) for the submit/poll side.

## Testing

- `tests/test_compute_ports.py` — the doorway + drift guards (adapter satisfies `SitePort`,
  every port method is a real weft tool).
- `tests/test_sites_config_write.py` — atomic write, aba-keys merge/preservation, roundtrip
  with the boot reader.
- `tests/test_compute_inference.py` — the proposal table over fixture capability records.
- `tests/test_compute_preflight.py` — classification, TOFU, the no-password key-setup contract.
- `tests/test_compute_router.py` — the endpoints over a fake `SitePort`; the weft-ui mount
  degrades-not-kills.
- `tests/test_compute_guide_tools.py` — the Guide tools' behavioral guards (no-password,
  explicit host-key consent, connect confirmation gate).
- `weft-ui/server/tests/test_embed.py` — shared-controller mode (serves the host's Weft, no
  ui.lock/reconcile; factory failure degrades the mount).

## Retention2 integration

Site durability is weft's `durable` key (`true` | `"/path"` | absent — a
user assertion, guessed but never decided by heuristics), set from the
Compute card's durable checkbox + "keep results at a safe path" pair
(`inference.build_site_config`). The local site declares `durable: true`.
close_run resolves weft's `retain.no_durable` refusal with a size-gated
policy (`content/bio/lifecycle/runs._no_durable_keep_policy`): small keeper
sets ship to `@workspace` with a note; larger ones become a Run
`retention_alert` carrying the levers. Kept files are addressable by the
`(run, relpath)` key (`data_register(run=, rel=)`); keeps anchor
re-obtainability after CAS eviction (verified live). Tests:
`tests/test_retention2_policy.py`, `regtest/datasets/epic_mechanism.py`,
`regtest/datasets/study.py` (live agent).

## Data-safety ledger + holdings

`core/data/ledger.py` is the ONE query layer for data safety: `data_ledger()`
(every valued item — datasets by home + durable declarations, retained runs —
in exactly one of safe / at_risk / changed / unknown) and `site_holdings(site)`
(kept results + dataset homes that live only there). Consumed by
`GET /api/projects/{pid}/data-ledger`, `GET /api/compute/sites/{name}/holdings`,
the `data_safety_summary` Guide tool, the LedgerStrip (Data/Results heads,
self-quieting), the ComputeTab consequence previews (Disconnect /
durable-uncheck / Free up) and the 3-class storage meter. It renders from
RECORDED state only — never probes sites. Tests: `tests/test_data_ledger.py`
(incl. the local-only quiescence contract), `frontend .../LedgerStrip.test.tsx`
(the UI snapshot half).

## Detached compute lane

Background jobs run on ANY declared weft site — including machines sharing
nothing with the controller (cross-OS validated: mac → linux). One lane, two
transports inside `WeftSubmitter`: shared-fs (unchanged W3.3 path) vs
detached, chosen by `site_contract()` (explicit `aba: contract:` wins; a
`host`-bearing yaml entry ⇒ detached; ad-hoc/undeclared ⇒ detached — never
guess shared-fs). Detached submit ships code AS DATA: a payload dir
{`core/jobs/detached_entry.py` — a stdlib-only, language-agnostic harness —
user script, spec + job-id memo nonce} registered into the CAS and staged by
weft as a task input; the task runs `python3 payload/aba_entry.py` under the
env's prefix when `env=` rides along. Envs realize per site; a platform
mismatch (weft surfaces it at realize) triggers ONE lazy re-lock
(`named_envs.ensure_platform`: recorded spec + site platform →
`env_ensure(update=True)`) and a transparent resubmit. Poll fetches
result.json + small outputs over the data plane into the local run dir →
standard controller-side harvest; big outputs stay on the node (kept,
`(run,rel)`-addressable, bring-back). Env-less runs are graded
(`env_grade: node-system` + runtime version on the exec record). Agent
surface: `run_python`/`run_r` `site=` (background-only, validated,
`site` wins over `execution`), `describe_compute.remote_sites`,
behavior-rule data gravity. Tests: `tests/test_detached_lane.py`,
`tests/test_detached_agent_inputs.py`, `regtest/datasets/multinode.py`
(live agent).

## Known gaps

- **Detached contract — largely CLOSED** by the detached compute lane (above): background
  python/R jobs run on detached sites with env parity. Still open from the original P2:
  `use_for`-driven AUTOMATIC placement (today `site=` is explicit), nextflow heads on
  detached sites, remote interactive kernels, and the Connect-flow copy that still calls
  detached "not yet supported" (update it). Preflight should also probe node runtimes
  (`python3`/`Rscript`) so describe_compute can report them per machine.
- **Lab templates (§5.7)** are read today from a deployment-level `$ABA_HOME/compute-templates.yaml`
  (`/api/compute/templates`). Bundle-scope composition (system → lab → user, like the catalog)
  is the intended source and remains to wire.
- **Multiple long-term stores → weft policy.** `policy.storage.large` is single-valued in
  weft; the tab already records the full list in the `aba: storage:` block, but only the first
  path flows into weft's role. Extending `storage.large` to a list is a small weft change.
- **weft-ui actor attribution.** In shared-controller mode audited actions carry aba's
  adapter `default_actor="agent"` (coarse) rather than weft-ui's own `"user"`; actor is
  constructor-only in weft. A per-request actor seam at the facade is the follow-up.
- **Cloud (P3)** — needs the provisioner + the `approval_pending`/budget gate; the Connect
  entry teases it disabled.
- **Regtest sweep.** The Guide connect tools ship with unit behavioral guards; adding a
  `regtest/placement`-style live-agent scenario for the connect flow is a follow-up.
- **Ledger `unknown` tier.** Honest unknown-on-unreachable needs RECORDED site health
  (freshness discipline forbids render-time probes); health isn't persisted yet, so items
  on an unreachable site keep their last derived state. Follow-up: persist per-site health
  from probe/verify/event traffic and derive `unknown` from it.
- **Placement line (§3) plan mode.** Record-mode facts ride the Run card's verdict +
  Details; the pre-flight "will run on X · ~N min queue" line waits on the P2 placement
  wiring (the [change…] picker is gated on it by the honesty rule).
