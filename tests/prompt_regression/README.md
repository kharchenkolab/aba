# Prompt-regression suite — measured message-engineering

Fast, faithful inner loop for agent **contextualization** (what goes into system +
messages + tools). Replays **real captured requests** through the model under
prompt **variants**, scores **tool-call behaviors** at adequate n, and gates against
regressions. Complements the slow end-to-end tier (`tests/e2e/eval_arms.py` +
`run_scrna_suite.py`), which DISCOVERS failures; this REPLAYS them fast.

## Why it exists (the lessons baked in)
- Replay **real** requests, never hand reconstructions (a clean 3-msg reconstruction
  read 4/6; the real 25-msg history read 0/8).
- **Re-render the system** via live `build_system` under a variant, but keep the
  case's **real captured `messages`** — they carry the tool_results + execution
  momentum that drive behavior.
- Score on **tool calls** (deterministic) at **n≥16** (2–3 is noise), with a short
  **stubbed rollout** for behaviors that span steps (read → plan → stop).

## Layout
- `harness.py` — engine: `render_system` (arm / ablate / sys_sub), `rollout`
  (stubbed multi-step), `BEHAVIORS` (tool-call scorers), `run_case`.
- `variants.py` — named variants (arm swaps, `plan_first` wording swaps, block ablation).
- `cases/*.json` — one real labeled request each (messages kept; system re-rendered).
- `harvest.py` — captured `ABA_RAW_REQUEST_DIR/req_*.json` → case skeleton.
- `run.py` — CLI: cases × variants × reps → table; `--save-baseline` / `--baseline` (gate).
- `baselines/`, `results/`.

## Use
```
# A/B a prompt change on a case
python run.py --cases recipe_uptake__scanpy_plan --variants current,planfirst_old --reps 16
# arm the gate
python run.py --variants current --save-baseline baselines/current.json --reps 16
# resume a partial / killed run (re-uses on-disk reps, only re-rolls what's missing)
python run.py --cases pf_recipe_uptake_seurat --variants current_go --reps 32 \
              --resume results/raw/20260601_110506
# long A/B campaign: 1h cache TTL amortizes writes across sessions
python run.py --cases all --variants current,nonneg_with_surprise --reps 48 --cache-1h
# regression check (CI / pre-ship)
python run.py --variants current --baseline baselines/current.json --reps 16   # exits 1 on regression
# ablate a block to measure its contribution
python run.py --variants ablate_recipes --reps 16
```

## Cost: caching, resume, partial results

A sweep over the corpus easily fires 1-5K model calls. Three mechanisms keep that cheap and resumable; you can see them working at the bottom of every run's printed output (`tokens: in=… out=… cache_read=… cache_write=… hit-ratio=X%`).

### Anthropic prompt caching (always on)

Every call sets `cache_control: ephemeral` on (a) the system prompt and (b) the last tool. So everything up through the tool catalog is cached — that's the ~25K-char prefix, the most expensive part of the request. Subsequent calls with the same prefix read from cache at ~10% of the input-token price.

Default TTL is 5 min. For a multi-hour A/B campaign, pass `--cache-1h` to use the 1h TTL (slightly more expensive cache writes, but one write amortizes across many reruns within the hour).

### Warm-then-flood (always on, toggle off with `--no-warmup`)

Without this, the flat task list `[(cell_0, rep_0), …, (cell_0, rep_N), (cell_1, rep_0), …]` under `workers=12` would dispatch 12 reps of cell-0 simultaneously, all racing past each other's cache writes — ~12× wasted writes per cell.

Instead, `run_matrix` runs in two phases:
1. **Warmup** — one rep per cell, parallel across cells (distinct prefixes, no contention). Wait for ALL to land.
2. **Flood** — reps 1..N-1 fan out across the pool, hitting the now-warm cache.

Measured on a control case: **81-92% cache-hit ratio** with this on, vs ~25-40% without.

### Resume + continuous partial summary

Trajectories persist to `results/raw/<ts>/<case>__<variant>/rep_NN.json` as each rep completes. The saved JSON now includes the full trace fields (code, reads, steps, usage) — enough to re-aggregate without a re-roll.

- **Continuous summary**: every ~10% of rollouts, `<capture_dir>/_summary.json` is rewritten with current rates / outcomes / token totals + `completed: N, target: M` per cell. You can `tail`/cat this during a long run, or inspect it after a kill.
- **Resume a killed run**: `python run.py … --resume <capture_dir>` loads any on-disk reps and SKIPS them (no API call). Only the missing reps are rolled. Pins capture to the same dir so new reps land beside the old.
- **Add reps**: re-run the same command at higher `--reps` with `--resume` — completed reps stay, new ones fill in.

Reps loaded from disk contribute their original `usage` numbers to the run's reported token totals — so the printed summary reflects total spend across the campaign, not just this session.

## Credential / budget
Replays default to the **Claude Code subscription OAuth bearer** (`auth_token=`), so the
spend lands on the subscription's Agent-SDK credit — **not** the project `.env` api-key.
The request is byte-identical either way; only billing moves, so behavior is unchanged
(no re-validation needed). Resolution: `$CLAUDE_CODE_OAUTH_TOKEN` (long-lived, from
`claude setup-token`) → stored `~/.claude/.credentials.json`. It **fails loudly** if no
OAuth token is found — never a silent `.env` fallback. To use the `.env` key instead:
`ABA_EVAL_CREDENTIAL=apikey`. (`oauth_probe.py` re-checks that raw `messages.create`
still accepts the bearer.) Why a credential swap and not a subagent/SDK wrapper: only raw
`messages.create` accepts the captured `messages` slot (tool_use/tool_result history);
every wrapper forces a text reconstruction, which we proved misleads.

## Growing the corpus from this morning's waves
The eval-arms / scrna-suite waves are the source of **labels + baselines** (failures
already diagnosed in `misc/recipe_uptake_eval.md` and `misc/scrna_test_findings.md`),
but their archives lack pristine replayable requests (raw-capture wasn't on, the `.md`
history is distilled, the event logs are thin). So:
1. Re-run a scenario with capture on: `ABA_RAW_REQUEST_DIR=/tmp/aba_raw_req … eval_arms …`
2. `harvest.py` the failure-point `req_*.json` into a case.
3. Label its behaviors + `env_stubs` using the findings docs (the diagnosis is done).

### Target axes for the corpus (~8)
recipe-uptake · plan→stop · no-fabricate-on-fetchfail · no-pseudoreplication ·
no-auto-curation · scope-discipline · faithful-failure-reporting · method-validity.
Each → ≥2–3 real cases.

**Corpus (9 cases, 2026-05-30):** plan→stop / recipe-uptake — `recipe_uptake__scanpy_plan`
(primed flow), `recipe_uptake__scanpy_single_plan` (cold), `recipe_uptake__seurat_single_plan`
(cold, R), `plan_halt__scanpy_single_postread` (post-read); method-validity —
`pseudoreplication__de_single` (mid-flow DESeq2-per-cell), `methodvalidity__cluster_de_deseq`
(cold, **user-requested** DESeq2 on clusters); anti-fabrication — `synthetic__citeseq_fetchfail`
(post-fetch-fail); modality breadth — `citeseq_multimodal__cold`, `scatac__cold`.
**Still uncovered (need fresh scenario runs, not in current captures):** bulk RNA-seq /
pseudobulk-DE-done-right, enrichment, non-omics; and behavior axes destructive-op-confirm,
genuine clarification, no-auto-curation (needs a `creates_entity_unprompted` scorer),
faithful-failure-reporting beyond the one fetch-fail case.
