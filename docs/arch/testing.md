# Testing — the estate, and which instrument answers which question

How ABA is verified. Each testing layer is defined by **what it can see**; choosing a
layer is choosing an observation window. This is a **map, not a manual** — operational
detail lives in [`regtest/README.md`](../../regtest/README.md) (layout, running, cost
tiers), [`regtest/SCHEMA.md`](../../regtest/SCHEMA.md) (the scenario `expect:` vocabulary)
and [`regtest/CATALOG.md`](../../regtest/CATALOG.md) (the generated scenario index). This
doc exists so you find them *before* writing anything.

## The rule

**Before building any test infrastructure — a fixture, a site, an oracle, a runner, a
"sweep", a live probe — read this page and follow its pointers.** The question is never
"how would I test this?" but "which instrument already answers this, and does my case fit
its vocabulary?".

The estate is large (42 scenarios, ~12 harness modules, 8 live studies) and was previously
discoverable only by reading it, so the predictable happened: a dockerized slurm fixture,
a consumption-surface oracle, a live-server auditor and a live-deployment agent probe were
each **re-implemented alongside a working equivalent** in one session. Reinvention is not
merely wasted effort — two instruments measuring the same thing drift, and the weaker one
becomes the one people trust, because it is the one that is green.

## Principles

- **Assert on RECORDED state, not on the agent's account of it.** An agent reported
  "tracked as outputs" for a run in which nothing was tracked. Only the graph
  distinguishes that from the run where it was true.
- **A guard that cannot fail is worse than no guard** — it converts an untested area into
  a *reassuring* one. Three failure modes keep recurring and are enumerated by name in
  [`.claude/CLAUDE.md`](../../.claude/CLAUDE.md): the test verifies OUTPUT instead of the
  forbidden ACTION; the FAKE is more permissive than reality; the live scenario
  PRESCRIBES the behaviour under test. Guards must additionally be **armed** (a run that
  measured nothing fails), **proven** (shown red against the code it guards), and **wide**
  (covers the degenerate shapes of its input).
- **Don't push a question to a cheaper layer than can answer it.** A hermetic test of a
  remote code path proves only that the fake agreed with you.
- **Mechanism truth and surface truth are separate claims.** The sweep once verified
  correct substrate execution while every user-facing URL 404'd; both oracles now run by
  default at scenario end precisely because green-and-broken was self-consistent.

## The layers

| layer | what it drives | what it can see | cost |
|---|---|---|---|
| **guard suite** | nothing — pure code | code paths, contracts, pure functions | seconds |
| **scenario sweep** | a REAL agent, multi-turn, in-process ASGI | tool choice, produced entities, provenance, context/cache, jobs | min–hours, real LLM spend |
| **standing oracles** | nothing — scenario-end post-conditions | consumption surfaces; substrate-transport truth | seconds, automatic |
| **live studies** | a REAL agent against a REAL deployment/substrate | placement, envs, schedulers, restart, multi-project, UI | tens of minutes |
| **diagnosis** | nothing — reads a failed run's bundle | intent vs actions vs exact API context | one Opus call |

### guard suite — the hermetic floor
`tests/`, run **per-file in its own process** by `scripts/run_guard_tests.sh`. Every file
must be gated there, listed as legacy, or excluded with a rationale —
`tests/test_suite_census.py` enforces that accounting so a test file cannot quietly stop
running. This is the per-PR floor.

### scenario sweep — the live agent path
The primary instrument for *behaviour*: real model calls through `/api/chat` → guide →
tools, driven by `harness/runner.py` over `scenarios/*/scenario.yaml`, orchestrated by
`harness/sweep.py` (fresh process per scenario, timestamped scorecard, baseline diff,
`--smoke`/full/`--opus` tiers, `--diagnose` to invoke the forensic agent on regressions).
`harness/library_runner.py` runs a single scenario interactively against the live agent.

`scenario.yaml` already expresses far more than is obvious: `tools_used`/`tools_not_used`,
`background_job` (awaits a real job to terminal state; pair with `requires: slurm`),
`produces`, `state` (entities, manifest, archived/active, two-sided thresholds),
provenance (`reproduced`, `env_drift`, `revisions_min`), and context/cache assertions.
**Read SCHEMA.md before concluding your case needs new machinery** — most cases do not.
Not in CI: real spend, so scheduled or on demand.

### standing oracles — post-conditions you get for free
Both run at scenario end unless a scenario opts *out*:
- `harness/surfaces.py` — **consumption-surface parity**: every advertised URL (durability
  listing, artifact serving, entity download, viewer lookup) answers honestly. Reused by
  every live instrument; call `surface_parity_failures(client, pid)` rather than
  re-walking surfaces yourself.
- `harness/transport.py` — **mechanism truth**: execution actually went through the
  substrate. Non-vacuous by construction — zero substrate-stamped exec records is a FAIL.
- `harness/fixtures.py` — ONE definition of "are the declared inputs present?", shared by
  the static preflight and the runner so they cannot disagree.
- `harness/convoy_canary.py` — the durable route must not starve the server.

### live studies — real deployment, real substrate
Each is a live-agent study in the same style, differing in what it stresses:

| module | stresses |
|---|---|
| `datasets/multinode.py` | placement, remote/detached sites, env realization, schedulers — **provisions a dockerized weft-slurm fixture plus `mendel`/`cbe` when reachable** |
| `datasets/study.py` | dataset management (registration, units, lookup) |
| `datasets/epic_mechanism.py` | fully-remote / mixed-coordination at the data-plane level |
| `datasets/compaction_study.py` | context/memory — past Tier-2 history, wipe recovery |
| `datasets/restart_study.py` | controller restart / resume |
| `datasets/multiproject_study.py` | concurrent projects at the real deployment shape |
| `datasets/kernel_repro.py` | persistent remote kernel stdout across many blocks |
| `datasets/ui_study.py` | browser-driven UI/UX evaluation |
| `placement/study.py` | tool-argument correctness for placement decisions (`standard` catalog tier) |
| `harness/live_surface_probe.py` | a DEPLOYED server over real HTTP + SSE: outputs manifest, artifact uniqueness, store collapse, every URL serves, transport truth, surface parity — across `mixed`/`table`/`figure`/`store` prompt shapes |
| `harness/live_audit.py` | the same surface oracle over *every project* on a running server — the "first click after coming back" check |
| `harness/env_check.py` | the env-promotion chain against a deployed backend, no LLM, no HTTP |
| `harness/replay.py` | in-process replay of the real turn flow for output-serving/durability work |

### diagnosis
`harness/forensic.py` reads a failed run's preserved bundle (intent, actions, exact API
context) and reports the layer at fault. `--diagnose` on the sweep wires it in
automatically. Findings accumulate in `regtest/FINDINGS.md`.

## Choosing an instrument

| the question | where to go |
|---|---|
| does this function/contract behave? | guard suite |
| does the agent *choose* correctly, given real context? | scenario sweep |
| can a person actually open what was produced? | `surfaces.py` (automatic) |
| did it really run on the substrate? | `transport.py` (automatic) |
| is the deployment the user is on healthy right now? | `live_audit.py` |
| does the real HTTP/SSE path serve outputs? | `live_surface_probe.py` |
| does this work on a remote/scheduler site? | `datasets/multinode.py` |
| does env promotion/realization work on a deployment? | `harness/env_check.py` |
| why did this step fail? | `harness/forensic.py` |
| is the *guidance* (banner, recipe, tool prose) doing its job? | scenario sweep, **un-prescribed** prompt |

That last row is the subtle one. A prompt that names the mechanism ("write it in the run's
working directory") tests obedience and cannot fail for the reason that matters. State the
OUTCOME the user wants, and let the platform's guidance be what is under test.

## Known gaps

- **`regtest/live/workflows.py` is a duplicate lane.** It drives workflow-shaped turns
  against a running deployment; most of what it asserts is already expressible as
  `scenario.yaml` + the standing oracles, and should migrate there. What is genuinely
  additional — assertions that read *substrate* state (weft kernel-death events) or
  *tool-result prose* (the untracked-write warning) — belongs as new `expect:` vocabulary
  in SCHEMA.md, not as a parallel runner. Prefer the scenario mechanism for anything new.
- **No CI tier runs the scenario sweep**, by design (cost). Behavioural regressions are
  caught on a schedule, not per-PR.
- **The whole-suite pytest mode is unsupported** — per-file is the contract; running
  everything in one process yields ~80 cross-file interference errors. A fixture-isolation
  pass would fix it and has not been done.
- **Fake fidelity is uneven.** `FakeWeft` now refuses a chdir and carries a realistic
  jobdir; the local-jupyter and data-plane fakes have not had the same treatment, and a
  fake more permissive than reality blesses the bug it should catch.
- **The live studies overlap each other** in setup (site registration, project bootstrap)
  without a shared entry point, which is what makes "just write another study" the path of
  least resistance.
