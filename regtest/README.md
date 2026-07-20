# regtest — live scenario regression & science-eval harness

Realistic, multi-turn scenarios run against the **live agent path** (`/api/chat` →
guide → LLM), scored mechanically + by an LLM vision rubric, to catch regressions in
how ABA builds context, uses recipes/tools, and produces + curates results.

This is **not** part of the pytest/CI suite — it makes real model calls and is too
expensive for per-PR CI. It's meant to be **kicked off on a schedule** (e.g. weekly)
or on demand.

## Layout
```
regtest/
  harness/
    runner.py           run ONE scenario (multi-turn, live path) → a forensic bundle + report.json
    forensic.py         Opus deep-dive on a FAILED step from a bundle
    library_runner.py   legacy v1 single-prompt runner (for the 4 v1-only scenarios)
    sweep.py            run ALL scenarios + scorecard + baseline diff + retention
    surfaces.py         consumption-surface parity oracle (scenario-end, default ON —
                        every advertised listing URL / artifact / download / viewer
                        lookup must answer honestly; see SCHEMA.md "Surface parity")
    live_audit.py       point the SAME oracle at a RUNNING server across all its
                        projects (the "first click after coming back" guard)
  scenarios/
    <id>/scenario.yaml  the scenario (v2 = multi-turn `steps:`; v1 = single `prompt`)
    <id>/_make_data.py  per-scenario data generator (some fetch real data)
    _make_data.py       top-level generator (the v1-origin synthetic datasets)
    _regen_all.sh       regenerate ALL data (cached: skips scenarios whose data/ exists)
    <id>/data/          GITIGNORED — generated on demand
    _runs/              GITIGNORED — forensic bundles (retention-pruned)
  baselines/            committed: last-accepted scorecard per mode (haiku.json / opus.json)
  reports/              GITIGNORED: per-run scorecards (JSON + .md)
  SCHEMA.md CATALOG.md FINDINGS.md   the scenario schema, index, and the living defect register
```

## Running

> **Oracle upgrade note (2026-07):** the harness now enforces consumption-surface
> levels automatically — per-step `produces`⇒served + pin⇒downloadable checks and
> a scenario-end `_surfaces` parity walk (see SCHEMA.md). The first sweep after
> this upgrade may fail scenarios that previously passed: those are latent
> product-surface bugs becoming visible, not harness regressions — triage them as
> product defects, then `--accept` a new baseline (the scorecard also gains one
> `_surfaces` row per scenario, so totals shift).

**First (or after a fresh clone): generate the data** — it's not committed.
```sh
bash regtest/scenarios/_regen_all.sh          # cached; ABA_REGEN_FORCE=1 to rebuild all
```

**Then sweep** (credentials come from `ABA_LIVE_ENV`, default `/tmp/aba_8000.env`):
```sh
python regtest/harness/sweep.py               # Haiku breadth (cheap; mechanical only)
python regtest/harness/sweep.py --opus        # Opus science (rubric judge on)
python regtest/harness/sweep.py --only tpm,survival
python regtest/harness/sweep.py --accept      # promote THIS run to the baseline
python regtest/harness/sweep.py --diagnose    # forensic on regressed FAILs
```
Each scenario runs in a **fresh process** (sequential) — a long-lived in-process
runner accumulates kernels/zmq sockets and destabilizes late scenarios. The sweep
writes a scorecard to `reports/`, **diffs it against `baselines/<mode>.json`**, and
exits nonzero if any scenario regressed. `--accept` updates the baseline.

**Run one scenario directly** (debugging):
```sh
ABA_SCENARIO=tpm python regtest/harness/runner.py            # Haiku
ABA_SCENARIO=tpm ABA_SCENARIO_MODEL=claude-opus-4-8 python regtest/harness/runner.py   # Opus
ABA_SCENARIO=tpm python regtest/harness/forensic.py [step]   # forensic on a failed step
```

### Portability — a fresh box, or the sweep against another deployment

Nothing is hardcoded to a specific home; a fresh checkout (or **aba-vbc**, running the
sweep against a VBC deployment) sets the vars it needs — all overridable:

| var | what | default |
|---|---|---|
| `ABA_LIVE_ENV` | NUL-separated `k=v` creds file (`ABA_LLM_CREDENTIAL`/`ABA_HOME`/…) the runner sources | `/tmp/aba_8000.env` |
| `ABA_SCENARIO_VENV` | python for the data generators — needs rdkit/skimage/Bio/anndata/tifffile | repo `.venv` → `$ABA_HOME/env` → `$PYTHON` → `python3` |
| `ABA_RUNTIME_VENV` | python for the scanpy generators + fallback | same resolution |
| `ABA_ENVS_DIR` | provisioning overlay (MUST be shared-FS under a `slurm` submitter — see envs.md) | `regtest/.envs_cache` (runner) |
| `ABA_PLACEMENT_STUDY_DIR` | placement-study output; `study.py` + `analyze.py` share this one var | `$TMPDIR/aba_placement_study` |
| `ABA_SCENARIO` / `ABA_SCENARIO_MODEL` | which scenario / model tier | `_selftest_session` / Haiku |

`_regen_all.sh` **fails loud with guidance** if no python with the scenario deps is found
(rather than silently FAILing every generator).

## Cost tiers & cadence
- **Weekly → Haiku breadth** (`sweep.py`): cheap; catches robustness + gross regressions.
- **Monthly / on-demand → Opus science** (`sweep.py --opus`): rubric-level quality.
- **On regression → forensics** (`--diagnose`): Opus deep-dive on the flagged steps only.

Scheduling + a stable unattended credential source are deliberately **not** wired
here — kick the sweep off manually (under your OAuth) for now.

## Data model
Committed footprint is tiny (~0.2 MB: generators + specs + a few static CSVs). Most
scenarios **generate synthetic data deterministically** (fixed seeds → stable planted
truth); 6 **fetch real data** at regen time (AF-DB/RCSB/Ensembl/NCBI/EBI/PubChem),
cached so a weekly regen doesn't re-hit the network. Scenarios are compute-bound, not
network-bound (see `SCHEMA.md`) — the point is to test ABA, not REST latency.

## Findings
`FINDINGS.md` is the living register — carry defects + resolutions across runs so we
don't re-discover them. Update it each cycle.
