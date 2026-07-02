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
summary: <one sentence>
data_files: [ ... ]            # staged into DATA_DIR at start
make_data: _make_data.py       # optional deterministic generator (seed=0)

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
