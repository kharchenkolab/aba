# HANDOFF — Eval corpus for the Record (finding pools + scenario scripts)

You are an orchestrator agent on a server with sustained compute. Your job is to
**author the evaluation corpus** for the Record — ABA's living-lab-notebook layer —
using subagents where this document says they help. You are building *content and
scripts*, not code: the eval runner/harness is a separate later task and is
explicitly out of scope here.

Suggested kickoff prompt for the human running this:

> Read `record-eval/HANDOFF.md` in this repo and carry it out end to end. Use subagents
> as it directs. Commit at stage boundaries.

## 0 · Required reading, constraints

Read first, in this order:

1. `RECORD_DESIGN.md` (repo root) — the design this corpus tests. Read all of it;
   pay closest attention to §5 (session lifecycle), §6 (gestures), §14
   (editorial governance, esp. §14.5 intent-precedes-evidence), §15 (open questions).
2. §1 (Primer) below — the minimal working vocabulary if you need a refresher
   while authoring.

Hard constraints:

- **No biology.** Nothing bio, biomedical, health, epidemiological, genomic,
  neuroscientific, ecological-organismal, or bio-adjacent — not as the transcribed
  paper, not as authored content, not as an incidental example. Physical earth
  science (climate, geology, oceanography) is fine; anything about organisms is not.
- **Write surface is `record-eval/` only.** Do not modify `frontend/`, `RECORD_DESIGN.md`,
  `ALTUI2.md`, or anything else. If you believe a schema in this document is wrong,
  do not change it — note the objection in `record-eval/REPORT.md` and follow the schema anyway.
- **Git:** commit at stage boundaries with succinct messages. No signature lines —
  no `Co-Authored-By`, no `🤖 Generated with…` footer.
- **Do not build the runner.** No fold implementation, no verifier engine, no CI
  wiring. `validate_pool.py` (provided) is the only executable check.
- **Do not download large datasets.** Pool A transcription works from the paper's
  reported results; the replication package must merely be verified to exist and
  be publicly downloadable.

## 1 · Primer — the Record in one page

The Record is a living manuscript that a scientist and an agent maintain over a
long-running data-analysis project. The agent continuously drafts and restructures;
**the agent proposes, only the scientist ratifies** ("agent proposes; only the user
writes"). The corpus you are building will be replayed through the future Record
implementation to test its editorial behavior.

Vocabulary used by the schemas below:

- **Question** — a top-level line of inquiry. Questions own sections of the
  manuscript. Threads of chat work are anchored to questions, but a session's
  findings may bear on *any* question (the anchor is an address, not a container).
- **Finding** — one unit of evidence produced during analysis: a claim plus an
  evidence artifact (figure/table/statistic/run output), with a strength.
  Findings are what sessions emit and what the narrative must absorb.
- **Session / sitting** — a stretch of chat work. Sessions end by attention
  (the scientist just moves on); there is no close ceremony. "Distill" is the
  moment findings get routed into the Record — it can happen mid-session
  ("distill so far") or when attention lapses.
- **Gestures** — lightweight per-message controls in chat. Two families:
  *curation* (acts on the Record immediately): `pin`, `fade`, `hold`, `draft_claim`;
  *investigation* (compiles a typed item into the plan): `check` (is this result
  sound?), `corroborate` (is it real — independent evidence?), `alternatives`
  (is it rightly explained?), `expand` (grow the direction), `plan`.
- **Plan items** — typed future work attached to sections; states
  `planned → taken-up → produced → absorbed`. The plan is the manuscript's
  future tense and the scientist's most direct control surface.
- **Governance** — structural changes are classed: Class 0/1 (agent may apply,
  visible), Class 2 (apply-with-notice, expires if unratified), Class 3
  (requires explicit consent, e.g. splitting a section, demoting to appendix).
  Salience has evidence floors: an "emphasize" indication on thin evidence
  converts to a stub + plan, it does not inflate prose.
- **Intent precedes evidence** (§14.5) — a scientist may declare a direction
  important before evidence exists; the correct response records intent and
  births a stub whose content *is* the plan.

## 2 · Deliverables and layout

```
record-eval/
  HANDOFF.md            (this file)
  validate_pool.py      (provided — do not rewrite; extend only via REPORT.md notes)
  pools/
    <pool-a-id>/
      pool.json
      SOURCE.md         (paper citation, links, accessibility check, transcription notes)
      scenarios/
        slow-burn.json
        ... (≥6 scenarios)
    <pool-b-id>/
      pool.json
      DESIGN.md         (the fictional project's internal story + number consistency notes)
      scenarios/
        ... (≥6 scenarios)
  REPORT.md             (final report — format in §8)
```

## 3 · The finding schema

`pool.json`:

```json
{
  "id": "kebab-case-pool-id",
  "title": "human title",
  "domain": "e.g. software performance engineering",
  "source": { "kind": "transcribed | authored",
              "paper": "citation (transcribed only)",
              "replication": "URL of public replication package (transcribed only)" },
  "questions": [ { "id": "Q1", "text": "the question as a scientist would state it" } ],
  "findings": [ ]
}
```

Each finding:

```json
{
  "id": "F07",
  "questions": ["Q1", "Q2"],
  "claim": "The p99 latency regression on /search is fully explained by the Jan-14 cache-key change: reverting it on a 5% canary restores p99 from 840ms to 215ms while p50 is unchanged.",
  "evidence": {
    "kind": "figure | table | stat | run",
    "caption": "p50/p99 latency, canary vs control, 48h window around the revert",
    "values": { "p99_control_ms": 840, "p99_canary_ms": 215, "p50_both_ms": 38 }
  },
  "strength": "weak | moderate | strong",
  "depends_on": ["F03", "F05"],
  "overturns": ["F02"],
  "notes": "optional authoring notes, not replayed"
}
```

Semantics that matter:

- `depends_on` — F07 cannot be discovered before F03/F05 exist. This DAG is what
  makes permuted scenario orderings coherent; orderings must be topological sorts.
- `overturns` — F07 contradicts and supersedes F02. Used by the contradiction
  scenario: the narrative must *revise with provenance*, never silently delete.
- `questions` — ≥20% of findings must bear on 2+ questions (anchor ≠ container
  is a design pillar; routing is one of the things under test). A few findings
  (2–4 per pool) should bear on *no* current question — tangential drive-by
  results that stress routing to notes/sediment.

## 4 · Pool A — transcribed from a published study

**Stage A1 — scouting (fan out).** Launch 3–4 scout subagents in parallel, one
per hunting ground:

- economics: AEA journals via the AEA Data and Code Repository (openICPSR) —
  replication packages are mandated;
- ML systems/benchmarks: NeurIPS Datasets & Benchmarks track, MLPerf analyses;
- astronomy: studies on public survey data (e.g. SDSS);
- energy / transportation / materials: studies on public government or industry data.

Each scout returns 2–3 candidates with: citation, links, confirmation the PDF and
replication package are actually publicly downloadable (verify by fetching headers,
not by downloading data), an estimate of how many distinct results the paper
reports, and whether the paper's own narrative contains any revision/contradiction
arc. **No bio, no health economics, nothing organism-adjacent.**

**Selection criteria (orchestrator picks one):** ≥15 distinct transcribable
results; a legible investigation arc (you can reconstruct roughly what was learned
in what order from the paper's own narrative); at least one internal revision
("we initially attributed X to Y, but…"); results legible to a non-specialist.

**Stage A2 — transcription (ONE subagent, not fanned out).** A single transcriber
reads the paper end to end and produces `pool.json`: each reported result becomes
a finding, in roughly the investigation's original order, with the dependency DAG
reconstructed from the paper's logic. Per-finding fan-out is *forbidden*: the DAG
and cross-finding numeric consistency are global properties, and splitting the
work fragments them. Target 30–45 findings. Write `SOURCE.md` alongside.

## 5 · Pool B — authored fiction: a performance-regression investigation

One subagent authors a fictional but rigorous investigation of a performance
regression in a distributed software service (chosen because the evidence
structure is legible without domain expertise). Requirements:

- 3–5 questions with a real arc (e.g. "what regressed?", "what caused it?",
  "why did our alerting miss it?", plus one that *emerges* mid-pool);
- 30–45 findings satisfying the same schema and quality bar;
- write `DESIGN.md` first: the ground-truth story of what actually happened in
  the fictional system, so all numbers are mutually consistent — then derive
  findings from it. Numbers cited across dependent findings must agree.

## 6 · Quality bar (both pools — validator-enforced where possible)

- Claims are specific and quantitative; every evidence stub has a readable caption
  and plausible values. No filler content anywhere.
- Strength mix ≈ 25% weak / 50% moderate / 25% strong.
- 2–4 `overturns` edges per pool.
- Longest `depends_on` chain ≥ 5.
- ≥20% multi-question findings; 2–4 no-question (tangential) findings.
- The pool must *support* every scenario shape it ships (e.g. contradiction needs
  its overturns mid-arc, not at the very end).

## 7 · Scenarios — orderings + overlays

Each scenario is a JSON file:

```json
{
  "id": "slow-burn",
  "pool": "<pool-id>",
  "stresses": "promotion timing — when a direction earns a full section",
  "description": "2–4 sentences: the behavioral story this ordering tells",
  "events": [
    { "t": 1, "type": "session_start", "anchor": "Q1" },
    { "t": 2, "type": "finding", "ref": "F03" },
    { "t": 3, "type": "gesture", "verb": "pin", "target": "F03" },
    { "t": 4, "type": "instruction", "text": "the cache angle is the important one — emphasize it",
      "expect": "salience raise where evidence supports it; floor-conversion to stub+plan where it doesn't" },
    { "t": 5, "type": "distill" },
    { "t": 6, "type": "clock", "advance_days": 6 }
  ],
  "assertions": [
    { "at": "event:12..20", "kind": "structure", "expect": "the cache direction is promoted from stub to section in this window" },
    { "at": "end", "kind": "structure", "expect": "outline depth ≤ 3; no section with fewer than 2 findings" },
    { "at": "end", "kind": "consent", "expect": "every Class-2/3 structural change has a matching ratification or expiry" }
  ]
}
```

Event types: `session_start {anchor}`, `finding {ref}`, `gesture {verb, target}`,
`instruction {text, expect}`, `distill`, `clock {advance_days}`. Gesture verbs:
`pin, expand, check, fade, corroborate, alternatives, plan, draft_claim, hold`.
Sessions have no close event — attention just moves (next `session_start` or
`clock`). `distill` mid-session is "distill so far".

Assertions are **semi-formal**: tagged English (`kind`: `structure | routing |
consent | salience | plan | provenance`) precise enough that the future runner can
formalize each into a programmatic check. Do not attempt formal syntax beyond this.

**Catalog** — ship ≥6 of these 8 per pool; `contradiction` and `proactive-intent`
are mandatory for both pools:

| id | stresses |
|---|---|
| `slow-burn` | promotion timing: evidence accumulates in one direction; when does it earn structure |
| `pivot` | direction change mid-project: demotion/appendixing of the old arc; Class-3 consent |
| `interleaved` | findings alternate among 3 questions; routing correctness; multi-question findings |
| `flood` | one sitting emits 8–10 findings; triage bounds; tray depth stays sane |
| `contradiction` | an overturn lands: narrative revises with provenance, never silently deletes |
| `proactive-intent` | scientist declares a direction before evidence: stub + plan, floor conversion |
| `absence` | a 3-week gap: re-entry briefing; structure held; timers paused |
| `busy-scientist` | drive-by micro-sittings, gestures without follow-up, nothing ever closed |

Orderings must respect the pool's DAG (`validate_pool.py` checks this). The same
scenario id on both pools should stress the same behavior with different content —
that redundancy is deliberate.

## 8 · Validation and report

**Stage V — after each pool and after scenarios:**

1. Run `python3 record-eval/validate_pool.py record-eval/pools/<id>` — must pass clean.
2. Launch two *independent* fresh-eyes critic subagents per pool (give them the
   pool + this document, not the transcripts of the authoring agents):
   - a **realism critic**: would a practitioner of this domain recognize this as a
     real investigation? Are the numbers internally consistent? Is anything filler?
   - an **adversarial critic**: is the pool too easy? Does each scenario actually
     stress what its `stresses` field claims? Could a trivial editorial policy
     (e.g. "never restructure") pass every assertion? Name the weakest scenario.
3. Fix what the critics find; re-validate; then commit the stage.

**`REPORT.md`** (final commit) contains: what was built (counts per pool/scenario);
validator output summary; the critics' surviving objections and what you did about
them; known weaknesses; schema objections if any (see §0); suggested next steps
for the runner implementation.

## 9 · Orchestration summary

| stage | parallelism |
|---|---|
| A1 scout papers | 3–4 subagents in parallel |
| A2 transcribe Pool A / B author Pool B | one subagent each, A and B in parallel |
| V validate + critics | 2 critics per pool, all 4 in parallel |
| S scenarios | one subagent per pool, in parallel |
| R report + final commit | orchestrator |

Commit at: end of A1 (scouting notes into `SOURCE.md` draft), end of A2/B, end of
each V pass, end of S, end of R. Succinct messages, no signatures.
