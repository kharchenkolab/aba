# Scenario schema (v2 — realistic multi-turn sessions)

v1 = a single `prompt:` + `expected:` (still valid; `_make_specs.py` manages those).
**v2** models a *realistic working session*: a biologist examines data, runs a few
analyses, **pins** figures, tweaks a parameter and re-runs, **restyles** a figure,
**deletes** a dead end, pins the keeper, comes back later, branches to compare.

The v2 runner (`regtest/harness/runner.py`) drives every step through the **real
live path** (`/api/chat` → guide → `core/llm.py` → Anthropic), so each step exercises
the genuine context assembly + prompt caching, and the runner asserts on the actual
API request (message growth, cache breakpoints), the `usage` cache tokens, and the
thread **manifest**/entities (was the figure pinned? result deleted? does the next
turn's context reflect it?).

## Two kinds of step: who acts
- **`actor: agent`** — a turn sent to `/api/chat` (the model does the work). Biologist-
  voiced `prompt`, never names a tool.
- **`actor: user`** — a UI/HTTP curation action the *human* performs on what the agent
  produced: **pin / unpin / delete / restore / modify_figure**. In ABA these are
  endpoints, not agent tools. The runner resolves a `target` (see Selectors) and calls
  the route, then checks the effect flows back into the agent's context.

## Step `kind`s
agent turns: `examine` · `analyze` · `revise` (modify the analysis) · `branch`
(explore an alternative — set `new_thread: true` to fork) · `drop` · `resume` (a LATER
session — the runner tears down + re-attaches to the same DB to test rehydration) ·
`version_change` (set `stage:` to swap/add data first).
user actions: `pin` · `unpin` · `modify_figure` (make_revision) · `delete` · `restore` ·
`reproduce` (re-run the exec that produced the target in the CURRENT env — provenance) ·
`delete_revision` (hard-delete ONE revision from a chain; tests re-parent/re-anchor).
NOTE: reverting to an old version ("go back to v2") has NO HTTP route — it's `set_current_revision`,
an agent MCP tool — so test it with an `analyze` agent turn and check `superseded_min`.

## `scenario.yaml`
```yaml
id: <kebab_id>                 # == dir name
title: <one line>
domain: genomics | protein | bioimaging | ...
gpu: false
requires: slurm                # optional — skip the scenario unless this submitter is active
summary: <one sentence>
data_files: [ ... ]            # staged into DATA_DIR at start
make_data: _make_data.py       # optional deterministic generator (seed=0)
#   Every declared data_files entry MUST be present in DATA_DIR after staging,
#   which means `make_data` must REPRODUCE it deterministically+offline — do not
#   depend on a gitignored, network-fetched artifact that a clean checkout won't
#   have. The runner asserts this before step 1: a missing seed exits 3
#   (SETUP-ERROR), which the sweep treats as unscored/infra (never a 0-score
#   regression, never baked into a baseline) — because a missing input makes the
#   agent refuse to fabricate, which must not be misread as product failure.

steps:
  - id: s1
    kind: examine
    actor: agent
    prompt: > biologist-voiced; NEVER names a tool/package/recipe
    expect:
      must_mention: [ ... ]            # substrings the reply should contain
      must_not: [ ... ]                # substrings that would betray fabrication
      produces: { figure: 1 }          # artifacts the turn should produce (>= n)
      checks: > human description of correct behaviour
      context: { msgs_grow: true }     # context/cache assertions (see below)

  - id: s4
    kind: pin
    actor: user
    target: { from_step: s3, select: figure, match: umap }  # which artifact
    expect:
      state: { pinned_results_min: 1, manifest_contains: ["UMAP"] }

  - id: s9
    kind: delete
    actor: user
    target: { ref: s4 }              # the entity/result produced by step s4
    expect:
      state: { entity_archived: { ref: s4 }, manifest_not_contains: ["UMAP"] }

expected_overall:
  planted_truth: > concrete, checkable ground truth in the data
  notes: > what a fully correct end-to-end session looks like
```

## Selectors (`target:`)
- `{ from_step: s3, select: figure|table|cell, match: <substr>, index: 0|last }` —
  pick an artifact produced by step s3's run (matched on filename/title; default last).
- `{ ref: s4 }` — the entity/result the runner created at step s4 (pins/revisions record their id).

## Checks (`expect:`)
- **text** — `must_mention` / `must_not` (coarse substring gates on the agent reply).
- **tools** — `tools_used: [name, …]` (the turn must invoke each) / `tools_not_used: [name, …]`
  (the turn must NOT invoke any). Use `tools_not_used: [run_python, run_r]` to keep an advice /
  lightweight turn compute-free AND to assert the agent answered *without* executing.
- **background job** — `background_job: {ok: true, stdout_contains: […], stdout_absent: […]}`.
  Awaits any run_python/run_r job the turn submitted with `background=true` to a terminal state
  (poll `/api/jobs/{id}`, then read its result), then asserts on its OUTCOME — ran clean, stdout
  has / lacks substrings — not merely that it was submitted. Pair with `requires: slurm` so the
  real Slurm `job.sh` path (module load + python-env sanitize) is exercised.
- **produces** — `{figure: n, table: n}` ≥ counts from the turn's run-artifacts.
- **state** (queried after the step via manifest + entities):
  `pinned_results_min`, `manifest_contains` / `manifest_not_contains`,
  `entity_archived:{ref}`, `entity_active:{ref}`, `entities_of_type:{figure: n}`.
- **provenance / versioning state**:
  `reproduced: true|false` + `env_drift: true|false` (read THIS step's `reproduce` result),
  `superseded_min: n` (count of `status=superseded` entities — proves a non-destructive
  revert hid newer revisions), `revisions_min: {ref: sX, n: N}` (the revision chain for
  the entity created at sX has ≥N entries).
- **context** (from the turn's raw API request + `usage`):
  `cache_breakpoints: true` (system stable prefix + last message carry cache_control),
  `cache_read: true` (this turn read from cache — empirical hit; only meaningful on turns ≥2).
  NOTE: `msgs_grow` is recorded as telemetry but is NOT a pass/fail gate — ABA does
  not monotonically accumulate messages; a `resume` rehydrates a compact, bounded
  context (e.g. 45 → 16 msgs) that then stays roughly flat. Treat n_msgs as an
  observable, not an invariant.
- `checks` / `expected_overall.notes` carry the real judgement for a human/LLM grader.

## Surface levels (automatic — scenarios opt OUT, not in)

The harness enforces the consumption-surface level uniformly, driven by what a
scenario already declares — authors cannot forget it and the 25 existing
scenarios get it without edits:

- **`produces` ⇒ served.** A step claiming `produces` implicitly promises those
  artifacts are CONSUMABLE: every produced artifact with a served URL must
  stream non-empty bytes at that step (200, or an honest 413) — a row that
  exists but doesn't open is a failure (`produces_served:<kind> -> <status>`).
  Per-step opt-out: `expect: {produces_served: false}`.
- **pin ⇒ downloadable.** A user `pin` step implicitly promises the pinned
  entity is reachable: its download must serve (200 with bytes / honest 413).
  Explicit form for other steps: `state: {downloadable: {ref: sX, ok: true}}`;
  `ok: false` asserts an HONEST refusal (4xx — never 200, never 5xx).

## Transport truth (scenario-end oracle, default ON)

After the last step the runner also walks the scenario's execution records over
the mechanism-truth surface (`GET /api/runs/{id}/execs`, `harness/transport.py`)
and asserts none self-identify as LEGACY executions (`compute.substrate` other
than the substrate). Rationale: outcome oracles cannot see mechanism — the
legacy local kernel lane and the substrate lane produce identical results,
records, and surfaces, which is how the platform ran the legacy lane by default
for months while every test stayed green. Reported as a synthetic `_transport`
row carrying `checked` (records examined) and `proven` (`checked > 0`). A
zero-checked pass proves nothing, so it is marked `proven: false` and printed
`UNPROVEN(checked=0)` — the scorecard can then tell "verified weft-clean" from
"verified nothing". By default an unproven step still scores PASS (flipping a
vacuous step would drop mech_pass and perturb an accepted baseline);
`ABA_REGTEST_TRANSPORT_STRICT=1` makes an unproven step FAIL. Opt out entirely
with a top-level `transport: false`.

## Surface parity (scenario-end oracle, default ON)

After the last step, the runner walks the CONSUMPTION surfaces for every run the
scenario produced (`harness/surfaces.py`): each file the durability listing
advertises must answer at its URL (200 with bytes, or an honest 413 naming
where/why — never a dead link), a `retained` row must carry a live URL, produced
artifacts with served URLs must stream, entity downloads must serve or refuse
honestly, and the viewer lookup must see viewer-eligible outputs. Reported as a
synthetic `_surfaces` step in the scorecard. Rationale: the per-step checks
verify RECORDS; without this, a scenario can compute right answers and record
right rows while every surface a person actually opens is broken. Opt out (rare
— e.g. a scenario that deliberately ends with bytes reclaimed) with a top-level
`surfaces: false`.

## Conventions (kept from v1)
Biologist voice, never name the tool. Planted, checkable truth (fixed seeds). Each
scenario directory is self-contained (`scenario.yaml` + optional `_make_data.py` + `data/`).
Realistic length: a session is typically **8–15 steps**, not 3.

## Network budget (important)
A good scenario is **compute-bound, not network-bound** — we are testing how ABA
assembles context, runs analyses, and manages artifacts, NOT the latency of public
REST APIs. Rules:
- **Default to local data** (generated or small pre-staged real files). The science
  should run without the network.
- **Bounded fetching is fine to exercise** (it tests discovery + `fetch_reference`/
  `fetch_ensembl`/`query-pdb`) — but only a **handful** of items, **fetched once**
  and registered/cached, never re-fetched every turn. A couple of PDB files or ~10
  sequences = OK; per-variant REST across many turns, or live InterProScan, = NOT OK.
- **Never let the network be the wall-clock bottleneck.** If a turn waits minutes on
  REST, the scenario is mis-designed — prefer a precomputed endpoint (e.g. UniProt's
  precomputed domain *features* over a live InterProScan scan) or pre-stage the result.
- Keep at most **1–2 deliberately network-exercising** scenarios in the whole suite;
  the rest should be local. (Flagged for redesign: `variant_annotation` repeats VEP
  REST per variant per turn; `protein_domains` blocks on live InterProScan.)
</content>
