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
- **Assert what the turn COST, not only what it achieved.** For a long time every check
  in the vocabulary described achievement — text, tools, artifacts, a clean job — so a
  scenario could reward doing more and never penalise doing far too much. Live
  2026-08-25: a request for a library the mounted base pack already contained and
  verified built a 2.0 GB duplicate environment over ~15 minutes, and every assertion in
  the suite was satisfiable by that outcome. `envs_created_max` and `step_seconds_max`
  (`regtest/SCHEMA.md`) are the two-sided half; use them on any step whose correct answer
  is *cheap*, and read `envs_created_max: 0` as "answer from what is already mounted".
- **Test the REQUEST, not the repair.** After a production incident the reflex is to
  guard the mechanism that was fixed. That guard encodes the model of the bug, so it
  passes whenever the model was incomplete — and the same user request fails again. The
  guard that cannot be fooled is the original request, replayed against the shipped
  artifact: hence `live_install_probe` and the `pack_provided_library` scenario, both of
  which enter exactly where the user enters.
- **A scenario that runs alone cannot see what only happens when turns overlap.**
  Every scenario here drives one thread to completion, so for a long time nothing
  crossed. The cross-project write leak (2026-07-27) was found only because two
  sweeps were run at once *by hand* — records, harvest dirs and artifacts filed
  under a bystander project, each row individually well-formed and the corruption
  visible only ACROSS projects. Concurrency is now a lane, not an accident.
- **The instrument can manufacture the failure it reports.** In the same sweep,
  ~10 "surface parity" failures per site were a client shim that JSON-parsed
  every body (so a CSV read as a dead link) and never pinned a project (so every
  durable view 404'd). A shim less capable than a browser invents breakage;
  suspect the instrument before the product when a whole class lights up at once.
- **A guard that cannot fail is worse than no guard** — it converts an untested area into
  a *reassuring* one. Three failure modes keep recurring and are enumerated by name in
  [`.claude/CLAUDE.md`](../../.claude/CLAUDE.md): the test verifies OUTPUT instead of the
  forbidden ACTION; the FAKE is more permissive than reality; the live scenario
  PRESCRIBES the behaviour under test. Guards must additionally be **armed** (a run that
  measured nothing fails), **proven** (shown red against the code it guards), and **wide**
  (covers the degenerate shapes of its input).
- **A live lane must check its own preconditions BEFORE it reports a verdict.** Arming
  is not only a guard-suite discipline. Three concurrent lanes once reported "did not
  recall their state" — a crosstalk-shaped finding — when the truth was that the
  deployment's tool catalog was empty and no lane had executed anything at all. A run in
  which nothing happened must say *that*, not answer the question it was asked. The
  concurrency lane now fails on the precondition and reports nothing else.
- **A deployment harness must LAUNCH the deployment, never re-implement its launch.**
  `verify.sh` boots the staged image with the same `apptainer run --containall` the OOD
  card uses, and drives it over HTTP — that is the only faithful way to ask "what does
  real ABA do?". Hand-rolled `apptainer exec` invocations written to "just test one
  thing" got the binds wrong every time (`/dev/fuse` absent so squashfs envs would not
  mount; `HOME` on the container's 64 MB tmpfs so the solver died "No space left on
  device"; `PIXI_CACHE_DIR` on a parallel filesystem where rattler's cache locking
  breaks) — and each failure was read as a product defect before it was recognised as
  the harness's. The corollary is that the card and the gate are two consumers of ONE
  launch contract: where they diverge, the gate tests a configuration production never
  runs. That contract is now a FILE, not an intention —
  `install/ood/aba/template/aba_launch.sh`, sourced by both, and by nothing else. When
  they were merely *supposed* to match, the gate ran without `ABA_BATCH_SUBMITTER`, so
  `submitter_name()` read an unset var inside `--containall`, returned `local`, and
  `--lanes wf_slurm_batch` passed having never submitted anything to Slurm. Divergence
  between two launchers does not announce itself; it reports success.
- **Don't push a question to a cheaper layer than can answer it.** A hermetic test of a
  remote code path proves only that the fake agreed with you.
- **A judgement measured once is not measured.** `est_gpu` came back true on three
  consecutive runs of the same request and false on the fourth, and the CPU run reported
  plain success — three greens hid it completely. Anything the model DECIDES (placement,
  sizing, whether to background) needs `--repeat` and a rate; a single green is a sample,
  and changing a prompt on the strength of one is tuning against noise.
- **Check that the scenario does not contain its own answer.** `cluster_idle_gpu_big_job`
  asks for work "~45 minutes on a GPU" — so a pass measures obedience, and the recognition
  it appears to test is never exercised. Its sibling `cluster_gpu_unhinted_training` states
  only the outcome. When a scenario is green and the live system is not, suspect the prompt
  first.
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
`background_job` (awaits a real job to terminal state; pair with `requires: slurm`, which
the sweep supplies and pre-flights — see `harness/preconditions.py`),
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
- `harness/preconditions.py` — the same shape for a scenario's `requires:`: ONE definition
  of what a declaration DEMANDS (the env that satisfies it) and whether this host can
  supply it. The sweep provides it per scenario and refuses up front when it cannot;
  without that, `requires: slurm` scenarios decline silently and the sweep reports green
  on a selection it never measured (all three did, for the life of a baseline).
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
| `placement/study.py` | tool-argument correctness for placement decisions (`standard` catalog tier). `--repeat N` runs each scenario N times and reports a RATE per field — placement is a model JUDGEMENT, so a single run samples a distribution and cannot describe it |
| `harness/live_surface_probe.py` | a DEPLOYED server over real HTTP + SSE: outputs manifest, artifact uniqueness, store collapse, every URL serves, transport truth, surface parity — across `mixed`/`table`/`figure`/`store` prompt shapes |
| `harness/live_install_probe.py` | **the science gate** (design + how to run: `misc/install_sweep.md`): asks a DEPLOYED server for a library, one real turn per package, and judges what the request COST — named envs created, wall seconds, and (with `--background`) which site the offloaded job landed on. Package set is data (`regtest/data/install_matrix*.json`); the `--pack-provided-only` scope is self-sufficient from the shipped packs' own `spec.verify`, so a missing data file cannot switch the gate off. Wired as `deploy.sh verify --install`, gating promote |
| `harness/live_audit.py` | the same surface oracle over *every project* on a running server — the "first click after coming back" check |
| `harness/env_check.py` | the env-promotion chain against a deployed backend, no LLM, no HTTP |
| `harness/replay.py` | in-process replay of the real turn flow for output-serving/durability work |
| `harness/concurrency.py` | did concurrent lanes actually OVERLAP (parallelism, max-in-flight)? The axis `--concurrent` was missing: it only checked that lanes don't corrupt each other, which strictly-serialized lanes also satisfy |
| `harness/install_stall.py` | **asymmetric** contention: one thread installing for minutes vs another wanting three lines of Python. Runs INSIDE the release image, because the install verb ABA uses exists only in the pinned weft — a developer checkout silently takes a different code path and reports "not reproduced". Times the `run_python` prologue phase by phase, so the answer is WHICH phase blocked |
| `harness/dispatch_latency.py` | **dispatch-stall screen**: which tool calls WAITED rather than worked (`queue_wait_ms` vs body time, plus the executor backlog). Read-only, no LLM — run after any session, live or manual |
| `harness/project_isolation.py` | **cross-project** audit: every recorded row belongs to a thread of the project holding it. Read-only, no LLM — run after any concurrent or multi-project live run |
| `live/workflows.py --concurrent N` | N threads in ONE project against one node at once |
| `live/workflows.py --cross-project N` | N projects at once, project creation staggered to land mid-flight, then the isolation audit |

### the deployment gate — the staged image, launched as the card launches it

`aba-vbc/verify.sh` is the only harness that runs the artifact production will run. It
boots the staged SIF headlessly on a random port and drives it over HTTP:

    ./deploy.sh verify                     boot tier — the image starts against this config
    ./deploy.sh verify --full              + live_surface_probe (real agent turns) + live_audit
    ./deploy.sh verify --install           + live_install_probe (the science gate)
    ./deploy.sh verify --lanes wf_slurm_batch,wf_cross_language_handoff
                                           + workflow lanes against THAT server

It reaches that fidelity by using the card's own two steps, unmodified:

| step | file | produces |
|---|---|---|
| resolve | `install/ood/aba/template/preflight.sh` → `aba_preflight.py` | `aba-env.sh` — the env block `site.yaml` implies (~19 exports: `ABA_BATCH_SUBMITTER`, `ABA_JOBS_GPU_ENV_PACK`, `ABA_MODULE_*`, `ABA_NEXTFLOW_*`, the credential chain) |
| launch | `install/ood/aba/template/aba_launch.sh` | the `apptainer run` argv — scope binds, the env store, `site.yaml binds:`, the slim base, the session `TMPDIR`, the deploy-injected forward list, the Slurm client/munge/NSS plumbing, the host module system |

Neither consumer builds argv of its own: the card adds one bind (its per-session SPA
dist) and the gate adds none, which `tests/test_launch_contract.py` asserts by COUNT so a
hand-rolled bind fails there rather than in production. The gate then overrides only what
must be throwaway — `ABA_HOME`, `ABA_RUNTIME_DIR`, `ABA_ENVS_DIR` — and points
`ABA_WEFT_PUBLISH_TREE` at the store under test. A bind the card gains is a bind the gate
exercises, with nobody remembering to add it.

`--lanes` passes `--base <the gate's URL>` to `regtest/live/workflows.py`, so the
workflow scenarios run against the deployment rather than a personal install. Use it for
anything that needs the real substrate wiring — scheduler offload, GPU routing, reference
acquisition. A lane that needs a site takes `LANE_SITE=<name>` (default `cluster`).

Promote is gated on this: `deploy.sh promote` refuses without a `.verified` stamp, and
records which tier wrote it, because a boot-tier stamp asserts nothing about surfaces.

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
| does anything break when turns overlap? | `--concurrent` / `--cross-project` + `project_isolation.py` |
| do the places that pin one external thing still agree? | a property guard listing every member (`test_lstar_lockstep.py`) |
| does the code behave against a library we can't install here? | a FAKE of that library at the subprocess seam (`test_viewer_store_contract.py`) |

Two of those deserve a note.

**A pin that appears in more than one file is a property, not a comment.** Five files named
the lstar version and prose said "bump these together"; two were a release behind anyway. The
guard lists every member, so adding a sixth consumer is one row and a forgotten one is a red
suite. The same shape fits any cross-repo version that must move as a set.

**When the library under test cannot be in the hermetic env, fake it — but faithfully.** The
viewer-store guards stand in for lstar, which lives only in the session env. The fake RAISES
on the calls the real one refuses (an eager read of a store, a read of field values), because
a fake that is more permissive than reality blesses exactly the bug you are guarding against.
Its assertions are on the ACTIONS taken — the source archive is not modified, a clean store is
not re-emitted, a defective one is copied rather than repaired in place — not on the result.

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
- **The concurrency lanes are opt-in and unscheduled.** `--concurrent` /
  `--cross-project` are the only instruments that can see an overlap defect, and
  nothing runs them automatically — so that class regresses silently between
  deliberate runs. They are also the slowest thing here (real turns, staggered),
  which is why they are not folded into the sweep.
- **A check can pass for the wrong reason, and passing is not evidence.** In the
  cross-project lane, run-card *attribution* was correct while the artifact
  *bytes* were written into a bystander project; two of three doors passed on the
  broken data. When a lane is meant to catch a class, verify each door RED against
  a recorded instance of that class, not just green against a fixed one.
- **The whole-suite pytest mode is unsupported** — per-file is the contract; running
  everything in one process yields ~80 cross-file interference errors. A fixture-isolation
  pass would fix it and has not been done.
- **Fake fidelity is uneven.** `FakeWeft` now refuses a chdir and carries a realistic
  jobdir; the local-jupyter and data-plane fakes have not had the same treatment, and a
  fake more permissive than reality blesses the bug it should catch.
- **The live studies overlap each other** in setup (site registration, project bootstrap)
  without a shared entry point, which is what makes "just write another study" the path of
  least resistance.
