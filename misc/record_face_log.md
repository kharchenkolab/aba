
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
