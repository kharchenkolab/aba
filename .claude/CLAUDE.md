# ABA — project notes

## What ABA is
ABA is an AI-orchestrated, **entity-oriented** workspace for data analysis: users and an AI agent ("Guide") collaborate through a shared, typed, persistent entity graph to carry out **long-term research projects** across the full cycle — data → analysis → results → conclusions → manuscript. Analysis outputs (datasets, runs, results, findings, claims) are first-class typed entities with provenance, so both humans and agents can focus at any level of abstraction and build on prior work — a research *partner*, not a notebook with a chatbot stapled on.

## Conventions
- the UI/UX should operate in terms of familiar domain entities and concepts for its users
- build robust, modular architecture
- suggest opportunities to implement more general or flexible solution by engaging AI agents on different levels
- use short git commit messages with no signature

## Basic truths (where things live)
- Recipes + know-how (references) live in the `kharchenkolab/aba-recipe-pack` repo: `recipes/<domain>/` (executable `bp-*`/named recipes) and `knowhow/` (advisory method/decision + reference docs). They're brought in at install / `aba update` into `$ABA_HOME/installation/` — that deployed copy is what the server reads; the repo is the source, so edit + PR there (branch work ships once it lands on `main`, pulled via `RECIPES_REF`). `search_skills` (BM25) indexes frontmatter only, so body-only edits are search-neutral.
- System prompts / rules compose bundle scopes system → installation → lab → user (narrowest-wins): universal always-on rules (e.g. `behavior.md`) live in `backend/system_bundle/rules/` (this repo); site/lab/user rules go in an `aba-bundle-starter`-derived bundle's `rules/`.
- Deployments update via `aba update` (ABA code from `main`); install paths/hosts are per-deployment, not recorded here.
- Architecture docs: `docs/arch/` — a succinct, code-cited doc per subsystem (index in `docs/arch/README.md`); `misc/*.md` are the design/evolution logs behind them.
- Arch docs are **coherent descriptions of the CURRENT system, never a journal**: no dated section headers, no "as built <date>" / "NEW" addenda — integrate changes into the existing narrative as if the doc were written today. History and rationale live in `misc/` and git; an arch doc may *link* there, not accrete.
- Consult the relevant `docs/arch/` doc before touching a subsystem; keep it true — update it + its **Known gaps** at any change that materially alters that part.

## Change discipline for shared agent inputs (tool catalog, prompts, context)
These are cross-cutting inputs to EVERY agent decision — a change has platform-wide blast radius and erodes quality silently if made structurally. So:
- **Tool-catalog rendering** is governed by ONE policy — `core/runtime/mcp/presentation.py` (per `prompt_mode`), consumed only by `gateway.list_tools(mode=…)`. Change a tier's rendering by editing its `_POLICY` entry, never by adding an `if compact` branch. See `misc/tool_presentation.md`.
- **Invariant:** the calling CONTRACT (param names/types/required/enum/default) is identical across all modes; only PROSE (docstrings, descriptions, titles) is tiered. Full prose is recoverable via `describe_tool`.
- **Never cut one tier to fit another's budget.** `standard` (grounded_guide, production, opus/1M) keeps full param prose; `lean`/`lean_small` (small local models) drop it for their own tight window — isolated.
- **Guards must be ARMED, PROVEN, and WIDE:** (a) a guard whose subject has a precondition
  (a budget crossed, a window engaged, a path taken) asserts that precondition fired —
  a run that measures nothing must fail, not pass (three instruments in one caching
  investigation read "nothing measured" as green); (b) a new guard is shown to FAIL on
  the code it guards against (stash-revert or equivalent) before it counts as coverage;
  (c) it covers the DEGENERATE shapes of its input, not just the one the fix was built
  for. Armed catches a test that measured NOTHING; this catches one that measured a
  single point. Enumerate, and cover what can occur in production:
  **absent** — the optional value missing where absence changes the PATH taken
  (`entity_id=None` is the *common* `view_artifact` shape, and made a vision ref resolve
  to nothing while 7 tests stayed green); **the other side** — a `>=` count check alone
  rewards producing more, so pair it with a ceiling; **the extreme of a tunable** — a
  bound that holds at default slack and breaks at its floor; **the unreached regime** —
  a 14-generation probe against a 30-generation window measures the wrong side of the
  window and reads as exoneration.
- **Every change to a shared agent input ships a BEHAVIORAL guard, not just a byte/structural test:** contract-invariance (`test_tool_presentation.py`, `test_lean_catalog_compression.py`), the lean budget ceiling (`test_lean_summary_budget.py`, lean-scoped), and — for any tier in production use — tool-argument correctness in the regtest sweep (`regtest/placement/` covers `standard`). Structural-only PRs to these inputs are insufficient.
