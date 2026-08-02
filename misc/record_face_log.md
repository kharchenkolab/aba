
## 16 · Phase-2 + live-agent pass (autonomous run, 2026-08-01, cont.)
OODA 4-8 on altui2: since-cursor (client-held lastVisit) + project title;
live tray riding derivePending (one-derivation parity); shared triage from
the face via classic /api/proposals endpoints with real undo; growth-arc
demo (6 stages, cross-stage accretion/integrity asserts) which surfaced +
fixed: dormant lines must hold strongest POSITIVE claim, sediment windows
at scale w/ honest total, per-section sitting lists window; live agent test
(sandbox backend on :8030, branch code, deployment python env + oauth):
2 real guide turns built a project viewed through the face — found maturity
lives in metadata.confidence not status (fixed via maturity_key seam),
agent models questions-as-claims absent advisor roles (S6 gap confirmed
live), added claims-held chip strip. Dismiss→undo through the face verified
against the real store. 306 frontend + 17 backend guards green.

## 17 · The story stratum reads (autonomous run, 2026-08-01, cont.)
Course correction from user content-critique: the page was "one-shot
questions separated by uninformative chat links" — narrative missing,
story polluted. Root causes fixed, each with the failure named first:
(1) prose projected titles-only -> `prose_body_key` seam ships
metadata.text as `body`, face renders it as the paragraph; (2) drafting
loop produced bare stubs -> record_draft payload carries composed prose
(compose_draft: strongest-first at maturity, negatives apart), scheduler
writes it into metadata.text; (3) per-section sitting lists sprayed raw
sit-thr ids -> one "worked N times · range" line (expandable), ordinal
human labels, ids confined to sediment/hovers; (4) growth demo had no
narrative arc -> six authored paragraphs arriving at story-days, stamps
in story time, asserts: story never empty past day 4, no id leaks into
prose. READER_RUBRIC.md added (record-eval/) — seven questions applied
by READING each stage; the pass caught a real invisible-content bug the
structural checks blessed (dormant holds chip at width 0 under a long
question — flex starvation; fixed with wrap + basis floor).

## 18 · LLM drafting behind record_draft (same run)
llm_draft flag-gated (RECORD_LLM_DRAFTS=1), charter distilled from S6,
mechanical gate (lists/maturity-free/id-leaks rejected; deterministic
composer backstops all failures). Content-eval drove 3 charter turns on
live drafts: v1 added confirmation/success language; v2 dropped that but
invented a mechanism clause (rule squeezed here, leak opened there — the
S6 pattern); v3 forbids both by name and grounds every clause in a claim.
Residual (inference no regex catches) is held by consent: drafts are
proposals. Verified end-to-end via real scheduler + face on the sidecar.
Suites: 306 frontend, 18+3 record guards, runner 114, census/seam green.

## 19 · The recursive Record (autonomous run, 2026-08-01, cont.)
User reframe adopted: the spine is not an organ — the Record is
progressively hierarchical and the top level is just one org level. Four
OODA tranches, each rubric-read on the growth arc before commit:
R1 recursion — parent_entity_id within the question set ships as
`parent`; adapter nests cycle-safe (orphans/cycles degrade to top-level);
one renderer any depth; TOC indents; dormant folds subtrees (+N). Demo:
q_tune stays nested with its own ratified paragraph, q_retry arrives
nested and is promoted by day 23 (creation-ordered flat list keeps the
accretion prefix stable through promotion).
R2 prose lifecycle — revisions supersede via wasDerivedFrom (narrative
gains the out-edge), heads-only reading surface + 'revision N' sig,
superseded rows kept for provenance; cites retire chips; advisor
staleness (drafted_claims) proposes revision, hand prose untouched. Demo:
mechanism v1+chips (st4) -> v2, chips retired (st5+).
R3 sitting freeze v0 — a note with sitting_of/run_ids IS the sitting
entity: frozen boundary, human label, out of loose notes; face renders
distilled rows as never-folding ratified one-liners. R4 distiller —
verbatim-paragraph questions (the live finding) get a kind-`question`
proposal with the crisp heading; mechanical ?-extraction, LLM behind the
flag; headings clamp to 3 lines as display defense. Demo: stage3 shows
the pasted-message heading + retitle in tray; stage4+ reads distilled.
Rubric catches this pass: flex starvation was avoided (R1 reused wrap),
and an IMPOSSIBLE DATE ('ratified · 2026-06-31') from day+1 arithmetic —
found by reading the page, fixed with real calendar math. Suites: 309
frontend, 21+5 record guards, growth-arc ALL OK, seam/census green.

## 20 · Sediment redesign + live scenarios (8h autonomous, 2026-08-01)
User verdict on sediment ("totally useless... gibberish") implemented:
thread grain — one folded row per NAMED line (title, counts, dates,
distilled label), runs behind a click, background grouped; raw ids off
the surface entirely; 60 rows -> one screen. Plans: open_questions render
as each section's plan block at every phase. Narrative links: grounded-in
chip line per paragraph (word-boundary trimmed), chat ▸ per section
(/p/<pid>/threads/t/<id>).
Live scenarios (sandbox :8030, fresh projects, generic data-analysis
domains) then drove FOUR platform fixes, each found by reading the face:
_ask_json bypassed core.llm credentials (silent D1/D2 death under
oauth_cc — chip filed for main); promotion.md lacked the explicit-ask
clause (guide refused "record this" politely, twice); my distiller
advisor duplicated D1 — removed per doctrine; claim titles are 80-char
truncations — claim_statement_key ships full statements, drafts weave
them (before: '...is driven by a (preliminary).'; after: real prose).
Verified live: inception -> D1-healed heading -> claims on request ->
drafter -> accept -> statement-grounded narrative -> staleness ->
revision 2 citing 3 claims -> second named line -> thread-grain sediment
showing two real ABA thread names. Open: guide doesn't file
open_questions on request (rule gap next); confidence stated by the user
doesn't carry into the claim (pack ladder starts preliminary — verify
intended). Suites: 309 frontend, 22+5 guards, growth ALL OK.

## 21 · Dataset re-analysis scenarios at scale (10h run, 2026-08-01)
Real data, real analyses (agent runs code), Record read at every step.
Findings -> fixes, all live-verified: (a) scratch-exec sessions file
NOTHING — promotion.md rule alone measurably insufficient, so a
deterministic on_stop detector rides the suggestion channel (tool-heavy
turn + runs + zero products -> nudge; accept -> results+figures filed);
(b) D2 convergence predated the result type (counted only pinned
figures — could NEVER fire on modern projects); fixed, then fired live:
'3 results point the same way' whose statement inferred the injected
ground truth (component repaired mid-season); (c) findings/results were
invisible to the face — claim role is now multi-type (claim+finding),
statement keys are candidates, and unaddressed entities reach their
question through the evidence they stand on (one-hop); (d) FIGURES
render in the story: evidence images resolve finding->result->exec
produced[] (the pin's (kind,idx) often addresses the text cell) and
render inline above grounded-in chips; (e) tray rows are evaluable in
place (proposal body as quoted detail) — and the popover had rendered
NO label text at all (flex starvation again; wrap + basis floor);
(f) D1 embedded volatile numbers in headings (stale +0.57 vs +0.397
beneath) — prompt forbids; (g) changelog dedup (create+update pairs).
Scenario arc: dataset -> characterize -> file -> threshold discovery ->
season extension with regime change -> agent detects the TRANSIENT
effect unprompted -> staleness revision -> cross-line check -> park with
open questions -> 6 simulated weeks (178 runs -> 3 named sediment
lines) -> week-away return reads as an 8-item deduped changelog.
(h) update_open_questions tool added — NO agent-side oq tool existed, so
the plan could never be agent-maintained; resolve matches the model's
own paraphrase (first live attempt missed on a longer paraphrase —
bidirectional + word-overlap match); verified: wake parked line ->
answer -> resolve + add new oq -> face plan shows produced/planned.
Triage round-trips exercised live: accept (draft/revision/convergence),
dismiss -> undo -> dismiss (rename suggestion).

## 22 · The cockpit projection lands (phases 5–9) (+8h)

The first hands-on user test returned the verdict the rollout had made
inevitable: "useless — both as an explanation of the current state of
the analysis, and as a navigation/control tool for running the analysis
itself." Root-cause: §13.3's phases 1–3 project the READING strata and
stop; the work loop (§5) and live anchoring (§6) had shipped only as
fixture theater behind `w.work`. §13.5 now maps §5/§6 onto the real
substrate (audited endpoint-by-endpoint — nearly everything needed
already existed) and schedules phases 5–10; this round built 5–9:

- **P5 truth & copy.** The drafting gate refuses mid-sentence
  truncation (two of three ratified narratives ended mid-clause at the
  old max_tokens=300); heads re-drafted through the normal
  proposal→accept path. Lifecycle words ("active") no longer
  impersonate maturities anywhere (world floors them to the ladder's
  first rung); chips show the pack's OWN vocabulary ("preliminary",
  never the fixture's "conjecture"). Plan block: plain words, visible
  glyph legend, explicit ✎. Sediment run rows ship their REAL produced
  images (exec-record join is thread+time-window — the id spaces differ
  in production: turn runs run_…, analysis sessions ana_…; the run_id
  join never fires. The first unit test used a matching id — a fake
  more permissive than reality) and rows with nothing to show offer no
  affordance. One right panel at a time; Escape closes.
- **P6 the instrument docks.** WorkDock: one panel, summonable from
  every noun — question, plan item, claim, figure, sediment line. The
  anchor's kind renders its pane (claim dossier with evidence ITEMIZED
  by name/thumbnail; figure with provenance + full-card link), below it
  the line's real transcript (markdown rendered; run outputs stitched
  into the working-step gaps by time), and at the foot the composer
  that RUNS: POST /api/chat, active-turn polling, cancel, awaiting-you
  → resume. Turn end → the world refetches. Workspace shell gained
  ?msg= (scroll-and-flash, machinery existed) and ?draft= (composer
  prefill).
- **P7 the page tracks the work.** Live-line lamps derive from real
  turn state (section banner + TOC ▶); the world refetches on a slow
  cadence while lines are hot (sediment accretes at launch); ▷ work on
  a plan item flips it taken_up at LAUNCH (dock onLaunched), acts stay
  available. Found + fixed en route: notebook.html was served cacheable
  — every rebuild stranded browsers on a deleted bundle hash (an hour
  of ghost-chasing; the "bug" in the launch flow was a stale bundle).
- **P8 gestures v1.** The investigation family (check · corroborate ·
  alternatives · expand) on the claim dossier — one-tap TYPED
  plan-item constructors (open-questions gained `kind`), receipts in
  place, items wear their verb. Pin on transcript answers files a note
  directly via /api/record/pin (typed by the pack's registered note
  role — core stays neutral).
- **P9 prose weaving.** The drafter receives the claims' image-bearing
  evidence (one-hop resolution, exactly as supports_index) and places
  [[figure:id]] markers at the point of mention; the gate treats
  markers as markup — strips them before the id-leak check, drops
  unknown ids, still demands complete prose and named maturities — with
  ONE corrective retry (measured failure: weaving dilutes attention and
  the maturity parentheticals drop). Live FigureEmbed resolves served
  artifacts and summons the dock; mentioned figures leave the trailing
  strip. The mature section now reads as a manuscript: statement →
  figure at its mention → next statement.
- **BareStart is real.** The day-0 box creates the first line and fires
  the first turn (threads + chat, two calls); the page leaves bare and
  wears the working lamp — the whole §5 loop now runs from the Record
  on a fresh project with no workspace visit.
