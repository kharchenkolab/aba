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
# regression check (CI / pre-ship)
python run.py --variants current --baseline baselines/current.json --reps 16   # exits 1 on regression
# ablate a block to measure its contribution
python run.py --variants ablate_recipes --reps 16
```

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
