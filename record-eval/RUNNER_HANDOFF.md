# RUNNER HANDOFF — replay engine, predicates, reference baselines

You are an orchestrator agent on a server with sustained compute. The eval
corpus under `record-eval/pools/` is complete (see `REPORT.md`); your job is to
build the **runner** that makes it executable: a replay engine that drives an
editorial policy over scenario event streams, a Record state model it maintains,
a predicate library that grades trajectories, and the trivial reference
baselines that give scores meaning. No LLM is required for the core scope.

## 0 · Required reading, constraints

Read first: `RECORD_DESIGN.md` (repo root — the semantics you are implementing;
closest attention to §5, §6, §7, §14), then `record-eval/HANDOFF.md` §1, §3, §7
(schemas — note the v1.1 consent-semantics paragraph), then `record-eval/REPORT.md`
(esp. §4 known weaknesses, §5 schema notes, §6 recommendations — you are
implementing its recommendations 1, 3, 4, 5), then a skim of both `pool.json`
files and 3–4 scenario files.

Hard constraints:

- **Write surface is `record-eval/runner/` only** (plus `RUNNER_REPORT.md` in
  `record-eval/`). Never modify pools, scenarios, `HANDOFF.md`, `validate_pool.py`,
  or anything outside `record-eval/`. If a scenario assertion won't compile or a
  pool detail seems wrong, record it in `RUNNER_REPORT.md` — do not "fix" the corpus.
- Python ≥ 3.10, **stdlib only** for the core (engine, state, predicates,
  baselines, fuzzing, tests). The optional S6 stage may use an LLM SDK.
- Tests runnable as `python3 -m unittest discover record-eval/runner/tests` (or
  pytest if you keep stdlib fallback working).
- Git: commit at stage boundaries, push to `origin/altui2` after each; if a push
  is rejected, `git pull --rebase` and push again. Succinct messages, **no
  signature lines** (no `Co-Authored-By`, no 🤖 footer).
- No biology anywhere, including in test fixtures and examples.

## 1 · Architecture — three pieces around one interface

**The policy interface is the load-bearing abstraction.** A policy answers the
editorial moments the engine generates; the real LLM agent (later) and every
trivial baseline (now) implement the same interface, so every future score is a
margin over named dumb strategies rather than an absolute number.

Shape it roughly as: the engine folds events; at each *editorial moment* it
calls the policy with the current read-only state view and the triggering
event, and the policy returns a list of **typed ops** which the engine applies
through one gate. Editorial moments (minimum): finding landed (foreground or
background), gesture made, instruction given, distill (produce/settle the
routing table), ratify/dismiss received, clock advanced (expiry, fades,
promotions due). Ops (minimum): create/promote/demote/merge/split section
(classed!), write/revise prose block (with maturity + provenance), route
finding (story/notes/sediment + question tags), create/advance plan item,
propose (enqueue classed proposal), apply-consented, add addendum, set
salience. Keep op vocabulary as small as you can while expressing what the
baselines and assertions need — record any op you add beyond this list.

**The engine gate enforces the two hard invariants as asserts** — this is
non-negotiable and lives in the transition code, not in tests:

1. *Consent conservation*: no Class-2/3 op applies without a matching consent
   event (an accepted proposal; `ratify` for decisions). Class-2 proposals
   auto-expire (unratified → lapse, never silently apply).
2. *Authored-text immutability*: prose marked scientist-authored or ratified is
   never rewritten by a policy op; revision arrives only as a dated,
   provenance-linked **addendum** proposal (REPORT §5.4: an overturn supersedes
   a claim's face-value reading, not artifacts derived from it — downstream
   citations get revised, not deleted; contradiction of *ratified* prose is the
   Class-X interrupt).

A deliberately misbehaving test policy must trip both asserts (write that test).

**The state model is a minimal but real Record**: outline tree of
question-owned sections (stub → section, maturity ○◐◕●), prose blocks
(placeholder text is fine — content quality is not graded; maturity, provenance
links, authored/ratified flags are), three strata (story / notes / sediment),
routing tables per distill, consent queue with classes and Class-2 expiry
timers, plan items with lifecycle, salience marks, superseded-but-findable
history. Semantics rules you must implement (all now stated in the design doc):

- Sessions end by attention: next `session_start` or `clock` closes the sitting.
- §14.7 absence: while the scientist is absent (clock advancing with no
  scientist events), content stays current (background findings land in
  sediment) but **governance timers freeze** — Class-2 proposals do not expire
  across a gap.
- §14.2 decisive-evidence rule: a controlled overturn may raise its structural
  proposal the same cycle (no hysteresis wait), but the consent class still applies.
- §6 discharge subsumption: one landing may discharge several scrutiny items;
  the routing row names every item it closes; provisional marks clear only when
  that row is accepted.
- Time: scenario `clock` events are the only time source (map pool-internal
  dates onto scenario time per REPORT §6.6; finding-internal dates are opaque
  content).

## 2 · Predicate library — bottom-up from the 106 assertions

Go assertion by assertion through all 16 scenarios. Add a predicate **only when
no existing one expresses the sentence**; stop when all 106 compile. Expected
neighborhood (REPORT §6.1): section-exists-in-window,
proposal-pending-and-unexpired, cited-under-all-tagged-questions-no-copies,
plan-item-lifecycle-state, provenance-link-reachable, prose-maturity ≤
claim-maturity, tray-bounded-and-typed, superseded-but-findable. End state:
~a dozen predicates, no generic assertion DSL.

Compiled assertions live in `runner/compiled/` keyed by (pool, scenario,
assertion index) — predicate calls with window parameters. **Scenario JSONs
stay authoritative and untouched.** A completeness check asserts every corpus
assertion has a compiled form. An assertion that resists compilation is a
corpus bug: file it in `RUNNER_REPORT.md` with your best reading, compile that
reading, and flag it — never skip silently.

## 3 · Reference baselines

Five policies, each ~a page, run through the same engine (REPORT §6.3 — several
corpus assertions were sharpened specifically to kill these):

| id | behavior |
|---|---|
| `inert` | routes everything to sediment; never proposes, never writes |
| `never-restructure` | writes prose, routes honestly, never any structural op |
| `append-to-first-question` | everything lands under the session anchor's section |
| `obey-overturn-labels` | mechanically executes revision on `overturns` metadata; otherwise never-restructure |
| `ignore-weak` | drops weak findings entirely; otherwise honest routing |

Plus one **sanity ceiling**: a hand-written "scripted-good" policy for ONE
scenario (pick `checkout/contradiction`) that does what the assertions expect,
proving the predicates *can* pass — without it, universal failure is
indistinguishable from broken predicates.

## 4 · Outputs

- **Differential matrix**: scenario × policy → pass/fail per assertion.
  Acceptance: every scenario is failed by ≥1 baseline; any scenario passed by
  all five is flagged in `RUNNER_REPORT.md` as non-discriminating.
- **Per-organ report** (REPORT §6.4): aggregate by assertion `kind` → organ
  (routing, consent ceremony, plan lifecycle, salience floors, provenance,
  structure) across scenarios, so a failure reads "the routing organ fails
  under X", not "scenario 7 red".
- **Fuzzing** (REPORT §6.5): generate N random dependency-closed orderings per
  pool (respect `depends_on`; reuse the topological check logic conceptually —
  do not import from `validate_pool.py`, keep the runner standalone), replay
  under the baselines, and check the order-independent families (routing,
  provenance, consent arithmetic) plus the two invariant asserts. Deterministic
  seed; N configurable, default 50/pool.

## 5 · Stages

- **S1** state model + engine + invariant gate + misbehaving-policy test.
- **S2** the five baselines + scripted-good; all 16 scenarios replay
  deterministically end-to-end under all policies (no grading yet).
- **S3** predicate library + compile all 106 assertions + completeness check.
- **S4** differential matrix + per-organ report + acceptance checks.
- **S5** fuzzing harness.
- **S6 (optional stretch — only if S1–S5 are done and pushed)**: a prompted-LLM
  editorial policy behind the same interface, flag-gated, reading
  `RECORD_DESIGN.md` as its system prompt, run on 2–3 scenarios once; report
  its column of the matrix. Isolate SDK use to this module.
- **R** `RUNNER_REPORT.md`: what was built; the final predicate list with a
  one-line meaning each; the differential matrix; corpus bugs found; ambiguous
  assertion readings; per-organ summary; suggested next steps.

Subagent guidance: S1–S2 are one coherent build — do them in one agent (the op
vocabulary, gate, and state model must be designed together). S3 can fan out
per-scenario *after* one agent establishes the predicate core on 3 scenarios.
Run an adversarial critic after S1 (does the state model honor RECORD_DESIGN
§5/§14 semantics? attack the gate) and after S4 (are the baselines honestly
implemented, or strawmen? is any pass vacuous?). Commit + push at every stage.
