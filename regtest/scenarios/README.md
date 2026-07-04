# Scenario library

Real, user-like prompts + (where relevant) realistic associated data + the
**expected findings** so a run can be checked. Used to regression-test the live
agent's behaviour: discovery, recipe uptake, tool use, correctness, and the
absence of fabrication.

> **Index:** [`CATALOG.md`](CATALOG.md) lists all scenarios (regenerate with
> `python _make_catalog.py`). Scenarios come in two shapes: **v1** single-prompt
> (below) and **v2** multi-step, *lifecycle-aware* — each step tagged
> `branch`/`drop`/`resume`/`revise`/`version_change`/`delete` to exercise ABA's
> project/provenance machinery, not just one-shot correctness. The v2 schema is in
> [`SCHEMA.md`](SCHEMA.md). v2 scenarios are hand-authored + self-contained (their
> own `scenario.yaml` + `_make_data.py` + `data/`); `_make_specs.py` only manages v1.

## Layout
```
regtest/scenarios/
  <id>/
    scenario.yaml     # title, domain, prompt, data_files, expected{must_mention, must_not, notes}
    data/             # the static input files the prompt refers to (DATA_DIR/<file>)
  _make_data.py       # regenerates the realistic data (fixed seeds → stable expectations)
  _make_specs.py      # regenerates the scenario.yaml specs + this README
  _regen_all.sh       # one-shot: regenerate ALL scenario data/ (run after a fresh clone)
```

## Data is generated, not committed
To keep the repo lean, the `data/` files are **gitignored** — only the deterministic
generators are committed. After a fresh clone, regenerate everything once:
```
bash regtest/scenarios/_regen_all.sh
```
Fixed seeds → the regenerated data reproduces each `scenario.yaml`'s planted-truth
byte-for-byte (verified). Exceptions kept committed (no generator / out of sync):
`survival/`, `reproduce_expr/`, `provenance_export/`, `revision_delete/`,
`version_revert/`, `_selftest_session/`. The runner prints a hint if a scenario's
`data/` is missing.

## Running
`regtest/harness/library_runner.py` loads a scenario, stages its `data/` into the
run's DATA_DIR, drives the live agent (Haiku), streams the transcript, and prints
a coarse PASS/FAIL by checking `expected.must_mention` / `must_not` against the
final answer. Detailed per-turn context dumps land in the run's turn-log dir for
deeper analysis.

    ABA_SCENARIO=enrichment .venv/bin/python -u regtest/harness/library_runner.py

`expected.notes` is the human description of what a correct result looks like
(the auto-check only does substring matching; real judgement is in the notes).

## Conventions
- Prompts are written the way a biologist would ask — they do NOT name the tool.
- Data is realistic (negative-binomial counts, log-normal abundances, real
  coordinates, a real GFP sequence), with a known planted truth so expectations
  are concrete.
- Add a scenario: make `<id>/`, add data to `_make_data.py` (if any) + a spec to
  `_make_specs.py`, regenerate.
