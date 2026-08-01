# RUNNER_REPORT — replay engine, predicates, reference baselines

Final report for the runner built per `RUNNER_HANDOFF.md`. All core stages
(S1–S5) are complete, tested (114 tests), and pushed at their stage
boundaries; S6 (the LLM policy) is deliberately deferred — see §7.

## 1 · What was built

`record-eval/runner/` — Python, stdlib only, ~6,800 lines including tests;
runs on the machine default 3.8 and on 3.13 with identical trace digests.

| module | purpose |
|---|---|
| `events.py` | frozen event/pool/scenario dataclasses + loaders (validate refs, DAG order) |
| `state.py` | the Record state model: sections tree (stub/section, maturity, main/appendix rank, closed), append-only prose with provenance/ratified/contested flags, classed consent queue with Class-2 active-age timers, plan items with §14.5 lifecycle + provisional scrutiny marks, routing rows/tables (routine vs decision), three strata, sittings/episodes with coalescing, salience/holds, absences, briefings |
| `ops.py` | the typed op vocabulary with **per-op class floors** (`CLASS_ORDER` 0<1<2<3<X); extra ops beyond the spec minimum: `mark_superseded`, `withdraw_proposal`, `add_briefing` |
| `engine.py` | the replay loop, gesture compilation (curation applied by the engine, investigation verbs → typed plan items), distill settlement, free-text ratify matcher, the absence/timer-freeze rule, and THE GATE |
| `policy.py` | the `Policy` interface — `decide(moment) -> [ops]`; moments: finding landed / gesture / instruction / ratified / dismissed / distill / clock / session_start |
| `baselines.py` | the five reference baselines |
| `scripted_good.py` | the hand-written sanity ceiling, keyed to checkout/contradiction (refuses other scenarios) |
| `trace.py` | per-event trajectory recording with full state snapshots + deterministic run digests |
| `predicates.py` | 17 predicates grading trajectories (§2) |
| `compiled/` | all 106 corpus assertions as predicate calls with windows + reading notes |
| `grade.py`, `matrix.py`, `fuzz.py` | grading CLI, differential matrix + per-organ report + acceptance, permutation fuzzing |

**The gate** (in transition code, per the spec): (1) consent conservation —
no Class-2/3/X effective-class op applies without a matching accepted
proposal; Class-2 proposals expire visibly and can never apply after lapse;
(2) authored-text immutability — ratified/authored prose is never rewritten;
revision arrives only as a dated, provenance-linked addendum through an
accepted proposal. Post-C1 hardening: op classes are floored per type (a
mislabeled `DemoteSection(cls="1")` is treated at its Class-3 floor), a
proposal's class must dominate every payload op's effective class (asserted
at propose AND at apply, so state drift between them cannot smuggle), and
`mark_superseded` escalates to Class X when live ratified prose cites the
finding. Eleven adversarial tests attack the gate; all trip it.

**Semantics decisions** (documented in docstrings, reviewable):

- *Absence rule*: consecutive clock events with no scientist-driven event
  between them form one attention gap; gaps ≤ 3 days accrue to Class-2
  timers (working cadence), gaps > 3 days freeze them (§14.7's "past a few
  days away"). Expiry at ≥14 active days, always a visible lapse.
- *Consent semantics v1.1*: distill accepts the routine (veto-tier) routing
  rows it presents; decisions accept only via `ratify` events, matched to
  pending proposals by token overlap on the free-text target (unmatched
  ratifies are recorded, never crash).
- *Overturn severity*: supersession of never-committed prose absorbs at
  Class 1/2; contradiction of ratified/committed prose is the Class-X
  addendum interrupt.
- *§14.2 hysteresis* is delegated to policies: the engine models no wait, so
  the decisive-evidence rule is enforced only on its consent-class half.
- *Claim drafts* are Class-"3" proposals by convention
  (`CLAIM_DRAFT_PROPOSAL_CLS`): decisions never expire, and only Class 2
  carries a timer; §14.1's table has no decision tier.

## 2 · The predicate library (17)

Built bottom-up from the 106 sentences; a predicate was added only when no
existing one expressed a sentence.

| predicate | one-line meaning |
|---|---|
| `gap_record` | absence logged honestly; background landings in sediment; re-entry briefing at/after gap end |
| `proposal_state` | a description-matched proposal is pending/accepted/expired, unexpired, accepted at a given t, applied, backing ratified prose |
| `ops_bounded` | no forbidden/over-class ops applied in a window (structure frozen; growth at Class ≤ 2) |
| `plan_item_state` | typed plan/scrutiny items reach lifecycle states; provisional marks set/cleared per §6 |
| `sitting_index` | coalescing, silent filing, mid-sitting distill, leftovers shelf, touched-set indexing |
| `salience_state` | pinned never fades; faded findable-not-carried; holds evaporate |
| `cited_under_questions` | multi-question findings cited under all tags, no copies (pending rows count as in-flight citations) |
| `routing_destination` | findings route to required strata, never into forbidden narratives (pending story rows violate too) |
| `overturn_handling` | absorb vs interrupt vs differential severity; overturn-triggered cascade re-exam; post-overturn corroboration |
| `face_value_retired` | no live prose still asserts a superseded reading; the finding stays findable |
| `section_promotion` | promotion proposed exactly once in-window, none before, with the required consent mode |
| `section_state` | section face at end-of-window (status/rank/cites/children/creation time) or global outline bounds |
| `stub_is_plan` | intent stub whose content is plan items, with no prose anywhere under the question |
| `tray_state` | tray bounded and typed at every in-window snapshot; non-empty at peak; destination rows exist per finding |
| `narrative_growth_bounded` | narrative-level changes within [min, max] for a window |
| `consent_conservation` | applied high-class ops rode consent; acceptances only at ratify/distill events; riders for open items/holds |
| `provenance_chain` | a ratified claim/section reaches all listed findings in one step |

## 3 · Differential matrix (final)

```
scenario                    append  ignore_wk  inert  never_rs  obey_ol  scripted
checkout/absence               2/6       3/6    1/6      3/6      3/6       -
checkout/busy-scientist        6/8       5/8    7/8      7/8      7/8       -
checkout/contradiction         3/6       2/6    3/6      3/6      5/6      6/6
checkout/flood                 5/7       6/7    5/7      6/7      6/7       -
checkout/interleaved           1/6       4/6    0/6      5/6      4/6       -
checkout/pivot                 1/6       1/6    1/6      1/6      2/6       -
checkout/proactive-intent      1/7       1/7    1/7      1/7      1/7       -
checkout/slow-burn             3/6       3/6    3/6      3/6      3/6       -
gaia/absence                   1/6       3/6    2/6      3/6      3/6       -
gaia/busy-scientist            7/8       6/8    4/8      7/8      7/8       -
gaia/contradiction             2/8       3/8    2/8      3/8      5/8       -
gaia/flood                     4/7       5/7    5/7      5/7      5/7       -
gaia/interleaved               1/6       5/6    0/6      5/6      4/6       -
gaia/pivot                     3/6       2/6    0/6      3/6      2/6       -
gaia/proactive-intent          1/6       1/6    1/6      1/6      1/6       -
gaia/slow-burn                 3/7       3/7    3/7      3/7      3/7       -
```

Acceptance holds: **every scenario is failed by all five baselines** (min
5/5, max 5/5); no non-discriminating scenario. The ceiling is honest where
it matters: scripted_good's 6/6 includes the only pass anywhere of the
Class-X-interrupt-plus-cascade configuration; obey_overturn_labels' 5/6 fails
exactly the overturn-triggered cascade — the discrimination the corpus was
sharpened for. Per-organ (pass-rate across all baselines): provenance
separates policies most (inert 3/16 → obey 11/16), plan lifecycle (4/15
flat) and salience floors (1/6 flat) are where no trivial policy can score —
they demand intent handling no baseline attempts.

Two critics ran (post-S1 on the gate, post-S4 on baselines/matrix). The
first found three real gate escapes (self-declared op classes, payload
smuggling under a benign description, Class-1 supersession of ratified
prose) — all closed with attack tests. The second found ~20 null tests and
~5 unpassable assertions; the fixable ones were fixed (live gate-violation
grading instead of crashes; pending routing rows counted as visible
destinations, which also un-broke the citation-timing windows; tray checks
peak-sampled over the window instead of post-settle; absorb no longer
satisfied by a merely-pending proposal; briefing checks windowed to the gap
end). The rest are inventoried in §5.

Fuzzing: 50 random dependency-closed orderings per pool × 5 baselines —
zero violations across all four order-independent families (gate, consent
arithmetic, routing consistency, provenance non-deletion); deterministic
under seed.

## 4 · Corpus bugs found (do-not-fix rule honored; corpus untouched)

1. **gaia/slow-burn[1] and [6]**: the stream contains no `ratify` event, so
   the asserted Class-3 promotion "ratified no later than the distill at
   event 41" / "carries explicit consent" is unsatisfiable under v1.1
   consent semantics (distill accepts routine only). Compiled as
   proposed-in-window, pending-or-accepted. The v1.1 retrofit added ratify
   events to five scenarios but missed this one.
2. **checkout/slow-burn[2]**: the t=32 ratify targets the claim draft, not
   the promotion — same gap, same compiled reading.
3. **Citation-at-distill timing** (fixed at the predicate layer, worth a
   corpus note): checkout/interleaved[0–2] and both flood[0] windows close
   before the settling distill, so citation-based readings are unpassable
   unless pending rows count as destinations. The runner adopts the
   row-is-the-receipt convention; a future corpus revision could instead
   move the windows past the distills.
4. **Busy-scientist rows mostly grade the substrate**: coalescing, silent
   filing, gesture compilation, and hold evaporation are engine work in any
   compliant implementation; the honest per-policy denominator in those two
   scenarios is ~3–4 of 8, not 8. The corpus is not wrong — it asserts
   Record semantics — but matrix readers should know those rows cannot
   separate policies (they separate implementations).

## 5 · Ambiguous readings and known nulls (54 reading notes total)

Every interpretive compilation carries a `reading_note` in
`runner/compiled/`; the flagged classes:

- **Model gaps** (engine lacks a first-class notion, compiled to the nearest
  checkable): standing pre-consent (§14.5) in both proactive-intent[last]
  cells (compiled as nothing-above-Class-2 after the declaration);
  provisional-mark inheritance along `depends_on` in gaia/busy-scientist[3].
- **Prose-quality clauses** the placeholder-text model cannot grade
  (briefing "ranked by consequence", citation "framing", refusal "stated in
  evidence terms"): compiled to their structural cores, noted per assertion.
- **Remaining nulls no legal policy can fail** (post-fix): the engine-
  semantics assertions in busy-scientist ([0]/[1]/[3] both pools, hold/fade
  mechanics), mid-sitting-distill in both floods, and the
  `consent_conservation` cells for gate-legal policies — these become live
  only for policies that trip the gate (now graded as universal failure
  rather than a crash) and are exactly the rows an LLM policy could
  plausibly fail where baselines cannot.

## 6 · How to plug in the real policy (S6 interface, ready)

Implement `Policy.decide(moment) -> list[Op]` (see `policy.py`); register in
`cli.POLICIES`. The moment carries the triggering event, a read-only state
view, and pool finding metadata. An LLM-backed policy needs: a renderer of
the state view + event into a prompt (RECORD_DESIGN.md as system prompt per
the handoff), a parser from the model's reply into the op vocabulary
(`ops.py` — 17 op types, class field optional since floors apply), and
nothing else: gate, grading, matrix, and fuzzing all operate on the same
interface. S6 was deferred deliberately: this environment hit its org API
spend limit twice mid-build, and an LLM policy run would be metered against
the same budget. The one-scenario smoke run the handoff suggests
(2–3 scenarios, flag-gated) is a ~30-minute task once budget exists.

## 6.5 · S6 addendum — llm_v0 measured (2026-08-01, laptop)

`runner/llm_policy.py` implements the S6 policy (subscription OAuth via the
backend's bearer resolution; registered in cli.POLICIES only when a token
resolves; matrix still runs baselines only). Charter = a distilled
editorial charter + a SELF-DESCRIBING op schema (introspected from ops.py).
Five iterations were needed before the gate stopped killing replays, each
one a named lesson now encoded in charter text or edge normalization:
classes are strings; class-2/3 ops only inside propose payloads;
apply_consented only for the moment's matched ids; proposal_cls must
dominate its payload (computed, not asked); question/section references
validated (models hallucinate Q5 in a 4-question pool). The edge guards
mirror how a production advisor is shaped: only proposals leave at class 2+.

Measured columns (assertions passed):

| scenario | llm_v0 haiku-4.5 | llm_v0 sonnet-5 | baseline range | scripted |
|---|---|---|---|---|
| checkout/contradiction | 3/6 | 3/6 | 1–5/6 (obey=5) | 6/6 |
| checkout/proactive-intent | 1/7 | — | 1/7 flat | — |

Same totals, different organs: haiku fails plan lifecycle, sonnet gets it
(PI1=planned) but malformed the claim-draft proposal; BOTH miss the
Class-X interrupt addendum — the differential the corpus was sharpened
for. proactive-intent ties the baseline floor: the v0 charter's
intent-precedes-evidence coverage (declare -> stub + plan items) is one
line where it needs a procedure; that is the next charter iteration, and
exactly the phase-3 drafting-advisor instruction set.

## 7 · Suggested next steps

1. **Run the LLM policy** on checkout/contradiction, checkout/pivot, and
   gaia/proactive-intent first — the three scenarios where every baseline
   floor is lowest and the organ coverage (provenance, consent, plan,
   salience) is widest; report its matrix column against the five baselines.
2. **Type routing rows at the source**: the engine currently types every
   policy row "routine"; letting policies mark decision-tier rows would make
   the tray-typing checks grade policy judgment, not substrate defaults.
3. **First-class standing pre-consent** (§14.5) and provisional-mark
   inheritance along `depends_on` would convert two model-gap readings into
   direct checks.
4. **Corpus v1.2**: add the missing ratify to gaia/slow-burn (bug §4.1),
   retarget checkout/slow-burn's t=32 ratify or add a second one (§4.2), and
   consider window adjustments per §4.3 — all three are one-line scenario
   edits that would eliminate the flagged readings.
5. **Fuzz with gestures**: the S5 generator emits findings/distills/clocks
   only; sprinkling random gestures (with their engine compilation) would
   fuzz the plan-item and salience machinery too.
6. **Wire CI**: `python3 -m unittest discover record-eval/runner/tests` +
   `python3 -m runner.matrix` + `python3 -m runner.fuzz --n 50` is the full
   behavioral guard; all three are deterministic and take under a minute.

## 8 · Commit trail

- `1d395d48` — S1+S2: state model, engine with invariant gate, 5 baselines,
  scripted-good; 45 tests, 81 deterministic replays
- `b9edf8c5` — C1: gate hardening per adversarial critic (class floors,
  payload consent ceiling, mark_superseded escalation); 56 tests
- `7c70e360` — S3: 17-predicate library, all 106 assertions compiled, grade
  CLI; 98 tests
- `a8776914` — S4: differential matrix, per-organ report, acceptance; 104
  tests
- `f14104b0` — S5: permutation fuzzing; 109 tests
- `9f4b18e3` — C2: live consent grading, pending-row visibility, tray peak
  sampling, absorb/stub/briefing tightening; 114 tests
- (this commit) — R: this report

Process note: S1/S2 and the two critics ran as subagents per the handoff's
orchestration guidance; S3–S5 and all critic-fix application were built
inline by the orchestrator after two subagent attempts at S3 stalled without
writing files. All writes stayed inside `record-eval/runner/` plus this
report; pools, scenarios, and the corpus validator were never modified.
