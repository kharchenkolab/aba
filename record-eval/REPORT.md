# REPORT — Record eval corpus

Final report for the corpus authored per `HANDOFF.md`. All stages (A1 scouting,
A2 transcription, B authoring, V1 pool critique, S scenarios, V2 scenario
critique) are complete and committed at their boundaries.

## 1 · What was built

Two pools, sixteen scenarios, all validator-clean (`0 errors, 0 warnings` on
both pools at final commit).

**Pool A — `gaia-bh1`** (transcribed). El-Badry et al. (2023), *A Sun-like star
orbiting a black hole*, MNRAS 518, 1057 (arXiv:2209.06833). Chosen from 11
verified candidates scouted in parallel across economics (AEA/openICPSR), ML
systems, astronomy, and energy/transport (full disposition table in
`pools/gaia-bh1/SOURCE.md`) — the only candidate whose PDF, data archive, and
reproduction notebook are all reachable with no login, with a genuinely
chronological investigation arc and honest in-paper revisions.

- 45 findings, 5 questions (formation Q4 and population Q5 emerge late,
  mirroring the paper), strengths 12 weak / 22 moderate / 11 strong
  (26.7/48.9/24.4%), 15 multi-question (33%), 2 no-question tangentials
  (F43/F44 — other teams' candidates re-characterized in passing), 3 overturns
  (first discrepant RV over the archival no-variation reading; joint
  RV+astrometry orbit over the Gaia-only solution; COSMIC posterior over the
  common-envelope default), longest dependency chain 12.
- 8 scenarios: slow-burn, pivot, interleaved, flood, contradiction,
  proactive-intent, absence, busy-scientist.

**Pool B — `checkout-p99-lb-idle-timeout`** (authored fiction). INC-2419: a
checkout p99 regression (310 → 1,620 ms at peak) caused by a netops
"connection-table hardening" change silently lowering an internal L4
load-balancer's idle-flow timeout 350 s → 60 s, turning idle pooled DB
connections into 1,521 ms landmines; misattributed for days to a pricing-service
deploy whose rollback "worked" only because it bundled a pod restart. Ground
truth, canonical numbers table, and consistency notes in
`pools/checkout-p99-lb-idle-timeout/DESIGN.md`.

- 40 findings, 4 questions (Q3 "why did detection miss it" emerges at
  position 29/40), strengths 9 weak / 21 moderate / 10 strong (22.5/52.5/25%),
  13 multi-question (32.5%), 3 no-question tangentials, 3 overturns
  (restart-signature analysis over the false fix; re-canary over the pricing
  attribution; per-AZ reconstruction over the rollup onset), longest chain 11.
- 8 scenarios (same catalog; same ids stress the same behaviors with different
  content, per HANDOFF §7).

Scenario-level totals: 16 files, 585 events, 106 assertions across all six
assertion kinds; every finding in both pools appears in at least one scenario.

## 2 · Validator output summary

Final state, both pools:

```
pool: gaia-bh1
  45 findings, 5 questions, strengths {weak:12, moderate:22, strong:11},
  multi-q 15, no-q 2, overturns 3, longest chain 12
  8 scenarios (25-55 events, 11-31 findings, 6-8 assertions each)
0 errors, 0 warnings

pool: checkout-p99-lb-idle-timeout
  40 findings, 4 questions, strengths {weak:9, moderate:21, strong:10},
  multi-q 13, no-q 3, overturns 3, longest chain 11
  8 scenarios (29-43 events, 14-27 findings, 6-8 assertions each)
0 errors, 0 warnings
```

One deliberate deviation from the §6 quality bar: Pool A has 2 no-question
findings rather than 2–4 *after* a critic showed F45's "tangential" status was
contestable (it is the deferred sixth candidate of the pool's own search, so it
was retagged Q5 to keep routing ground truth gradable); 2 is within spec. Pool
B's strength mix moved off exact 25/50/25 (to 22.5/52.5/25) when F10 was raised
to moderate so the pivot scenario's doomed direction genuinely earns structure —
a deliberate trade of cosmetic ratio for eval validity.

## 3 · Critique process and what it changed

Four independent fresh-eyes critics reviewed the pools (realism + adversarial
per pool, given only the pool and the handoff), then two more reviewed the
scenario sets. Everything they raised was either fixed or is recorded below.

**Pool A realism** (verified all 45 findings against the paper, glyph-level PDF
decoding): found one substantive transcription error (a rotation-period bound
with the sin(i) factor inverted — fixed), one overstated claim (F04 pre-empting
its own downstream findings — softened), a caption normalization error (F28,
fixed), and a SOURCE.md longest-chain exemplar citing a nonexistent edge
(fixed). Everything else checked out: all three overturns are the paper's own
reported revisions; cross-finding numerics (masses, periods, eccentricities,
mass functions) agree.

**Pool A adversarial**: the sharpest structural finding was that every overturn
was *cushioned* — its warning findings were DAG-ancestors, so no valid ordering
could stage a blindside contradiction. Fixed by relaxing two edges
(F10→[F07], F35→[F32]) so uncushioned orderings exist while the canonical
order stays valid. Also: F20's Q4 tag now backed by a real F32 edge; F13's
decorative Q3 tag dropped; F01's Q5 tag dropped to keep Q5's late emergence
honest; two weak findings given downstream weight.

**Pool B realism** (full arithmetic recomputation): two blockers — the
dead-borrow flow physically exceeded what a 12-pod connection fleet could arm
(fixed by scaling the fictional service to 100 pods; every rate and percentage
survived, and DESIGN.md now carries the stock-vs-flow arithmetic), and
"peak-only" was contradicted by the pool's own midday/evening data points
(fixed with an explicit daypart exposure profile and a re-timed stage-1 onset
that now *corroborates* the cohort model — an overnight config push shows no
step until the morning ramp re-arms the pools). Also fixed: the 1,500 ms
deadline re-attributed to a client wrapper (stock pgJDBC `socketTimeout` is
seconds-granular), the 504 mechanism surfaced in-pool (retry re-draws a dead
connection 20% of the time → 3,042 ms > 2,500 ms deadline), exact compound
exposure arithmetic (per-borrow 1.42% → 4.20%), a budget-exhaustion timestamp
recomputed under a stated model, and the night-restart event reframed as the
version-independence control (it cannot exhibit the relapse signature).

**Pool B adversarial**: one blocker — a finding whose claim text cited a
finding id that does not exist yet in most legal orderings (F37 "(F40)") —
plus ancestor-cone knowledge leaks (F33, F37), hindsight-voiced claims that
pre-labeled the misattribution arc as false (defusing pivot and contradiction;
F10 rewritten contemporaneous and upgraded), and self-routing tangentials
(classifier clauses stripped from F09/F36 so routing is a real decision).

**Scenario critics** (one per pool, post-S): both found the sets DAG-clean with
zero mis-referenced assertion windows, and both converged on the same two
systemic issues, fixed across all 16 files where they applied:

1. *Ratification could not be expressed.* Several consent/provenance assertions
   presumed an acceptance act the event schema lacks. Fixed with a stated
   convention (in each affected scenario's description): routine routing rows
   are accepted at each distill (veto-tier); decisions — claim drafts, addenda,
   Class-3 changes — are ratified only by explicit `instruction` events. Where
   an assertion needed a ratified artifact, an explicit instruction event now
   grants it (see §5 for the schema note).
2. *Upper-bound-only assertions.* Several scenarios (flood in both pools,
   B-absence, B-busy-scientist) could be passed by an inert do-nothing policy.
   Fixed with lower bounds: floods now require the mechanism/characterization
   narrative to actually grow and a claim-grade draft to exist; absence
   scenarios now provoke a pending Class-2 proposal *before* the gap so
   timer-pausing is armed (Pool A's gap additionally receives background
   sediment landings, testing the fast-clock/frozen-governance split of
   §14.7); busy-scientist gained an empty drive-by sitting so silent filing is
   actually exercised.

The Pool A scenario critic also overturned an earlier V1 decision: `pivot` had
been dropped for Pool A on the argument that a monotone success story has
nothing to demote. The critic showed this conflates pivot with *failure* — the
pool supports a Q1-closure pivot (once F29/F30 settle "is it a black hole," the
vetting machinery steps down to appendix rank and attention re-centers on
formation/population — exactly what the published paper's appendix structure
did). That pivot was authored; it simultaneously closed the
Class-3-demotion coverage gap and put the previously unused findings
(F29/F30/F37/F40–F42/F45) to work. Both pools therefore ship all 8 shapes.

## 4 · Surviving objections and known weaknesses

Recorded as authored; none blocks use of the corpus.

1. **Pool A's first act is nearly a fixed script.** F01→…→F12 is close to a
   single mandatory chain (the F01–F12 subgraph admits ~1,960 of 4.8e8
   orderings), so every scenario replays a similar first third; ordering
   diversity lives post-F12, where the ready-set spans all five questions. This
   is inherent to transcribing a discovery whose early logic is truly
   sequential. Scenario authors spent their diversity accordingly.
2. **Salience pressure in Pool A is overlay-supplied.** Strength correlates
   with narrative centrality (the spine is strong, the periphery weak), so a
   "salience = strength" heuristic reproduces near-ideal placement from the
   evidence stream alone; the evidence-floor behaviors are exercised through
   instruction overlays (targeting the weak F21/F22 age story and F41/F42
   forecast pair), which is where the design says the signal enters anyway.
3. **Pool A's Q1/Q3 tags overlap** (Q3 is nearly a sub-question of Q1);
   fine-grained Q1-vs-Q3 routing assertions would be mushy, so scenarios
   discriminate Q2-vs-Q1 and Q4-vs-Q5 boundaries instead.
4. **Pool A's tangentials are one species** (other-candidate
   re-characterizations hanging off F01) — one routing decision rehearsed
   twice rather than two distinct stresses.
5. **Pool B's contradiction machinery tests response, not detection.**
   `overturns` edges are pool metadata handed to the system under test, and
   the overturned findings are graded/worded honestly enough that a
   label-obeying policy executes the revision mechanics correctly; what it
   cannot fake is the ratified-prose addendum path, the live dependent-claim
   cascade (the contradiction scenario reorders F20 before F15/F16 so the
   onset revision hits a still-committed attribution), and the Class-3 consent
   arc. The one unlabeled tension left in the pool is the deliberate 4.3% vs
   4.2% rounding seam (F06 vs F21).
6. **The B-slow-burn scrutiny discharge is a judgment call**: F22 is asserted
   to discharge both the check and corroborate items on F17 (counter-scale
   measurement subsumes the manual join); a runner could reasonably require a
   distinct discharge for the soundness check.
7. **A same-sitting Class-3 demotion proposal** (B-pivot) sits in tension with
   §14.2's N-consecutive-cycles hysteresis; the scenario description records
   the reconciliation (a controlled overturn is decisive evidence, not
   optimizer noise), but the runner will need that rule.

## 5 · Schema objections (per HANDOFF §0 — noted, not applied)

1. **No ratification event type.** The event vocabulary (`session_start,
   finding, gesture, instruction, distill, clock`) cannot express the
   scientist's accept/dismiss acts, which the Record's consent machinery is
   *about*. We worked around it with the distill-accepts-routine /
   instruction-ratifies-decisions convention stated per scenario, but the
   runner would be cleaner with first-class `ratify {target}` / `dismiss
   {target}` events (or an `accept` field on `distill`).
2. **Gestures carry no free text.** `plan` (and aimed variants of other verbs)
   compile typed items whose *content* matters to plan-lifecycle assertions,
   but the schema is verb+target only. Scenarios either pair the gesture with
   an instruction or loosen the assertion to existence+lifecycle. A `note`
   field on gesture events would fix it.
3. **Background landings have no explicit form.** The A-absence scenario emits
   `finding` events between `clock` events with no enclosing session to model
   machine work landing in the sediment during an absence (§5: runs land at
   launch without a sitting). The validator accepts this, but the runner needs
   the convention stated: a finding event outside any session is a background
   sediment landing.
4. **`overturns` needs a stated severity convention.** F03 in Pool A has 36
   transitive dependents; a runner reading "overturned" as "invalidated,
   propagate provisionality" would mark most of the record provisional. The
   corpus-wide convention (stated in scenario descriptions): an overturn
   supersedes the claim's face-value reading, not the artifacts derived from
   it — downstream citations get revised, not deleted. Severity is contextual:
   supersession of never-committed prose is Class-1/2 absorption; contradiction
   of ratified prose is the Class-X interrupt (the A-contradiction scenario
   asserts the differential explicitly).

## 6 · Suggested next steps for the runner

1. **Formalize the assertion vocabulary bottom-up from these 106 assertions.**
   They cluster into ~a dozen predicate families (section-exists-in-window,
   proposal-pending/unexpired, cited-under-all-tagged-questions-no-copies,
   plan-item-lifecycle-state, provenance-link-reachable-in-k-steps,
   prose-maturity ≤ claim-maturity, tray-bounded-and-typed,
   superseded-but-findable). Building those primitives first, then compiling
   each `expect` sentence to them, beats inventing a generic assertion DSL.
2. **Add the ratification events** (§5.1) before writing the replay engine;
   retrofit these scenarios mechanically (the conventions are stated per file).
3. **Score differentially against trivial policies.** The critics' named
   baselines — never-restructure, append-to-first-question, obey-overturn-
   labels, ignore-weak, inert-do-nothing — should be implemented as reference
   policies and *run*; a scenario is only as strong as the baselines it fails,
   and several assertions were explicitly sharpened to kill specific baselines.
4. **Report per-organ, not per-scenario** (RECORD_DESIGN §13.4): the assertion
   kinds map to organs (routing, consent ceremony, plan lifecycle, salience
   floors, provenance) — aggregate across scenarios by kind to see which organ
   of an editorial policy fails.
5. **Permutation fuzzing.** The pools' DAGs admit far more legal orderings than
   the 16 authored scenarios (Pool B is 71.7% order-free pairwise; Pool A
   post-F12 similarly). Once the runner exists, generate random
   dependency-closed orderings and check the ordering-independent assertion
   families (routing, provenance, consent arithmetic) on all of them; the
   authored scenarios then remain the behaviorally-targeted core.
6. **Timestamp discipline.** Pool B findings embed absolute dates (Jun 2026);
   the `clock` events are relative. The runner should map scenario time onto
   the pool's internal calendar per scenario rather than assuming they align —
   or treat finding-internal dates as opaque content (the F38 load-test date
   was stripped for exactly this reason).

## 7 · Commit trail

- `f610b15b` — A1: scout 11 candidates, select Gaia BH1 for Pool A
- `2f5a130c` — B: author Pool B (checkout p99 / LB idle-timeout, 40 findings)
- `3d0ca236` — A2: transcribe Gaia BH1 into Pool A (45 findings)
- `c5d12743` — V1: apply critic fixes to both pools
- `559c9b2f` — S: author 15 scenarios; validator clean
- `e1addb58` — V2: scenario-critic fixes; pivot shipped for both pools; full
  finding coverage
- (this commit) — R: final report

Process note: the four V1 pool critics and two V2 scenario critics ran as
independent fresh-eyes subagents per HANDOFF §8; two authoring subagents were
lost mid-fix to an org spend limit, and their outstanding fix lists (recorded in
this conversation's critique results) were applied directly by the orchestrator
and re-validated. All content constraints held throughout: no biology anywhere
in the corpus; writes confined to `record-eval/`.
