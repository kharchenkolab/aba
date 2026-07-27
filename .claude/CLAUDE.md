# ABA — project notes

## Content isolation protocol (safeguard hygiene) — MANDATORY, from turn one
ABA is a GENERIC data-analytics framework. Assistant output and context must
stay domain-neutral: model safeguards flag accumulated life-science content,
and once the context window is contaminated the flags fire on EVERY turn —
including no-op turns — degrading the session via model switching. Residue
cannot be removed after the fact, so prevention is the only control:
- NEVER print into the main context: live session transcripts, chat/message
  logs, tool-catalog docstrings (backend/content/bio/mcp_servers/**),
  recipe/knowhow files, live project entity listings, or run outputs. These
  carry domain vocabulary even when the task is generic.
- Anything touching those goes through an ISOLATED SUBAGENT that reports
  structural facts only: opaque handles (DS-1, PKG-N, SKILL-N, OUT-N),
  error codes/stages, counts, timestamps, file:line of platform code. Site
  and entity IDs are fine; names/titles/topics are not.
- Test fixtures and examples the assistant writes must use generic names
  (data.parquet, figs/scatter.png, siteA — never domain-flavored ones).
- Platform code (core/, lifecycle, routes, tests) is generally safe to read
  directly; content-pack prose is not. When unsure, subagent it.
- A flag mid-work is a signal to STOP forward edits, park work safely
  (WIP branch + spec file), and hand off to a fresh session — never to push
  through with a switched/degraded model on critical code.
- Long investigations: externalize state continuously (git commits, WIP
  docs, misc/ notes) so any session can be abandoned without loss.

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
- **Three failure modes that keep defeating ARMED/PROVEN/WIDE — check for each by name:**
  (a) **The test verifies the OUTPUT, not the forbidden ACTION.** A fresh-kernel guard
  asserted the banner appeared; the buggy code produced it too, via the very branch that
  was killing remote kernels. When a change means "achieve X *without* doing Y", the
  load-bearing assertion is `not Y` — assert on the call log, not just the result.
  (b) **The FAKE is more permissive than reality, so it blesses the bug.** A fake kernel
  accepted a `chdir` the real one dies on; a fake sandbox started EMPTY while every real
  jobdir carries the driver's machinery, so a harvest filter shipped that scooped
  `current_block` as a Run's only output. A fake must REFUSE what the real thing refuses
  and CARRY what it always carries; make it raise, so the class cannot pass again.
  (c) **The live scenario PRESCRIBES the behaviour it means to verify.** A multinode
  scenario told the agent to write "in the run's working directory" — so it tested
  obedience, and stayed green while the guidance it should have been testing said nothing
  about outputs at all. State the OUTCOME the user wants; never the mechanism under test.
- **Prefer a PROPERTY guard over an instance fix when a mistake has recurred.** Five
  separate spots injected a controller-only path into remote-bound code (DATA_DIR, the
  reticulate pin, `_ensure_kernel_cwd`, the cwd probe, the orientation banner) because
  remote-awareness was a per-call-site decision. `tests/test_remote_setup_purity.py`
  asserts the PROPERTY over the assembled artifact instead, so a new contributor that
  forgets fails without anyone remembering to test it.
- **A handle one door emits must open at every door that takes one.** Doors were each
  tested alone and all passed; nothing fed one door's output into another's input, which
  is where three frictions lived (`run_r` returned an `/artifacts/…` URL `view_file`
  rejected; `view_artifact` had no site branch). `tests/test_handle_round_trip.py` is the
  matrix — adding a door or a handle shape is one line.
- **Every change to a shared agent input ships a BEHAVIORAL guard, not just a byte/structural test:** contract-invariance (`test_tool_presentation.py`, `test_lean_catalog_compression.py`), the lean budget ceiling (`test_lean_summary_budget.py`, lean-scoped), and — for any tier in production use — tool-argument correctness in the regtest sweep (`regtest/placement/` covers `standard`). Structural-only PRs to these inputs are insufficient.
