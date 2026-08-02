# The Record face — a document-first projection of a project

An alternative Contact-plane face: the project rendered as a living lab
notebook (design: `RECORD_DESIGN.md`, repo root). Phase 1 is **read-only** —
a projection of the entity graph, no new writes, no new entities — so any
existing project can be viewed as a Record today, and the classic workspace
and the Record stay two renderings of one substrate (§13.3 of the design).

> Status: current as of 2026-08. Phase 1 (read-only face) + phase 2
> (shared triage: tray from the proposals store, accept/dismiss/undo via
> the classic endpoints, client-held what's-new cursor) + phase 3
> authoring strata (recursive org axis, prose bodies + revision lifecycle,
> drafting/distilling advisors, sitting freeze v0) + phases 5-9 of the
> COCKPIT projection (design §13.5): the work dock, live turn tracking,
> gesture constructors, pin, and manuscript figure weaving. "The spine"
> is not a separate organ: it is the org axis viewed from the root.

## Aims & invariants

- **A face, not a fork.** The Record reads ONLY through the graph read-port
  and existing stores; ratifying anywhere is the same proposal row. Nothing
  here may grow domain state of its own.
- **Core knows roles, never type names.** Which entity types play the
  Record's roles (question / claim / prose / note) arrives by registration
  from the content pack — the same seam as the type registry and card
  builders. One `# noqa: seam` literal exists (the role *name* "claim").
- **Honest projection.** Organs the substrate can't fill yet render empty,
  never mimed — the fixture face (frontend `notebook.html` without `?live`)
  remains the design-complete reference.
- **The org axis is recursive.** A subquestion is a question whose
  `parent_entity_id` is another question (threads carry no edges); the
  World ships `parent`, the adapter nests cycle-safe (a broken chain
  degrades to top-level — no node is ever lost), and ONE renderer draws
  any depth. Dormant parents fold their whole subtree to one line.
  Promotion/demotion are moves in depth, not schema changes. The strata
  (story/notes/sediment) are NOT levels — they exist at every node; the
  attention organs (tray, what's-new, desk) are reader-scoped and never
  multiply per node.
- **Sittings are derived until distilled.** The episode grain is clustered
  from run rows by attention gap (`SITTING_GAP_MINUTES`). A note carrying
  `sitting_of` + `run_ids` is a DISTILLATION record: that sitting is an
  entity — frozen boundary (its runs leave the clustering pool), human
  label on the face (a ratified one-liner that never folds), and it exits
  the loose-notes stream.
- **Ratified prose is never rewritten.** A revision is a new prose entity
  pointing `wasDerivedFrom` at the one it supersedes; sections read heads
  only ("revision N" in the signature) while superseded rows stay for
  provenance and search. Prose `cites` claims; a cited claim's chip
  retires into the story.
- **The face ACTS through the workspace's own endpoints — never its own
  write path.** The dock's composer posts `/api/chat` (SSE fired, then
  polled home via `active-turn`; cancel and `awaiting_user`→resume
  surfaced); plan items launch through the same call and flip
  `taken_up` at launch; gestures write typed `open-questions`; the bare
  face's begin is `POST /api/threads` + `/api/chat`. The one Record-own
  write is `POST /api/record/pin` (message excerpt → note, typed by the
  registered note role). Ratifying, dismissing, planning here and in
  the classic UI are the same rows.
- **One right panel, summonable from every noun.** The WorkDock's anchor
  names what summoned it (question / plan item / claim / figure /
  thread); the anchor's kind renders its pane — a claim opens as a
  DOSSIER (statement, standing, evidence itemized by name with
  thumbnails), a figure opens with provenance + a full-card deep link —
  and below every pane: the line's real transcript (markdown rendered,
  run outputs stitched into the working-step gaps by time) and the
  composer. Escape closes; opening elsewhere retargets (one ▶).
- **The page tracks the work.** Live-line lamps derive from real turn
  state (section banner + TOC ▶, probed per section); while any line is
  hot the world refetches on a slow cadence, so sediment accretes at
  launch and evidence counters move while you watch.

## The model

```
content pack ── register_record_roles({question: thread, …}, ladder, artifacts)
                                 │
GET /api/record/world  ──►  core/record/world.assemble_world()
  (require_project)             │  reads: find_entities / edges_* /
                                │  runs_port / proposals_store / audit
                                ▼
        World v1 JSON: questions · claims(+rung) · prose · notes ·
        sediment.runs · sittings (derived) · whats_new · tray · leftovers
                                │
frontend notebook.html?live=1 ──► live.ts apiToWorld() ──► the mock renderer
```

- **Maturity** = the pack's ladder, ordered at registration. It may live in
  entity metadata rather than the status column (`maturity_key` — bio's
  claims keep it in `metadata.confidence`; the platform status column is
  lifecycle). The assembler ships `maturity` + rung; the renderer picks the
  glyph. Pre-prose, sections surface held claims as a chips strip
  (`Section.claimsHeld`) that retires as prose lands and cites them.
- **Prose bodies** ride the same seam: `prose_body_key` names the metadata
  key carrying a prose entity's readable body (bio: `metadata.text`,
  narrative.yaml). The World ships it as `body`; the face renders it as the
  paragraph, title as stand-in when absent. The story stratum is therefore
  REAL prose, not entity titles.
- **The story stratum reads.** Per-section episode history renders as one
  summary line ("worked N times · date range", expandable); row labels are
  ordinal + date + size. Internal identifiers (`thr_…`, `sit-…`, `run_…`)
  never appear inline in story sentences — they live in hovers, doors, and
  the sediment table. `record-eval/READER_RUBRIC.md` is the content-eval
  instrument: applied by reading each growth-arc stage as the scientist
  would, at every face change.
- **Leftovers** = the §13.1 edge-complement: artifact-typed entities
  (registry `is_artifact`, passed at registration) with no
  includes/supports edge either direction, unpinned, unarchived.
- **Sittings** = per-thread clustering of `runs` rows; unthreaded runs are
  background landings, never sittings; timestamp-less runs coalesce (they
  cannot prove a gap).

## Key implementation references

| Where | What |
|---|---|
| `core/record/world.py` | the assembler; role/ladder/artifact registration; `derive_sittings` |
| `core/graph/runs_port.py` | the runs read-port (raw SQL stays inside `core/graph/`) |
| `core/web/routers/record.py` | `GET /api/record/world` (+ `require_project`) |
| `content/bio/record_roles.py` | this pack's role map: thread/claim/narrative/note + `is_artifact` sweep |
| `frontend/src/notebook/live.ts` | API World → renderer World adapter; `fetchLiveWorld` |
| `frontend/src/notebook/dock.tsx` | the WorkDock: anchor panes, transcript, gestures, pin, the composer that runs turns |
| `frontend/src/notebook/main.tsx` | `?live=1[&api=…][&project=…]` opt-in; fixture face is the default |
| `tests/test_record_world.py` | the guard suite (gated): projection, sittings, tray, leftovers |
| `frontend/src/notebook/live.test.tsx` | adapter mapping + renderer smoke over an adapted world |

## Live-agent findings (2026-08, sandbox)

Multi-turn scenarios over fresh projects, each read through this face:
(1) the default thread absorbs the user's whole first message as its
`question` — healed by the pack's D1 detector from turn 2 once
`_ask_json` was routed through core.llm credentials (it silently died
under oauth_cc, which had also muted the convergence detector); the
three-line heading clamp covers turn one. (2) The guide would not record
findings on explicit request until promotion.md gained the
"explicit ask IS the bar" clause — it obeyed the promote-sparingly rule
faithfully; the rule was incomplete. (3) The guide's identity gates
curation by domain: it declines to file claims for out-of-domain topics
(scenarios must be data-analysis-shaped). (4) Claim display titles are
hard 80-char truncations; drafting from them produced broken prose —
hence `claim_statement_key`. (5) The full loop verified live end-to-end
on an agent-built project: messy first message → crisp heading (D1) →
claims on request → drafter proposal → accept → statement-grounded
narrative → third claim → staleness revision → revision 2 citing all
three. (6) Open-question filing on request still does not happen —
plan items reach the face only when something writes `open_questions`;
the next instructions gap to close.

## Phase-3 slice (2026-08): the first advisor + record-write kind

`content/bio/record_advisor.py` — the drafting-during-work role, v0
deterministic: post-turn (on_stop), a thread with >=2 claims and no
narrative gets a `record_draft` proposal (signature carries the claim
count, so dismissal holds until the world changes). The proposal payload
carries DRAFTED PROSE (`compose_draft`: the thread's claims woven
strongest-first at their maturity, negatives set apart); accepting — from
either face — creates the narrative with `metadata.text` = that draft
(scheduler kind `record_draft`, undoable), which the story stratum then
renders as a real paragraph. Verified live end-to-end: agent turn ->
advisor -> tray -> face accept -> prose renders under the question.

The advisor also handles the STALENESS half: when claims land after the
head narrative was drafted (`drafted_claims` marker), it proposes a
REVISION (payload `revises` + recomposed text + `cites`); hand-written
prose — no marker — is never second-guessed. Question naming is NOT this
advisor's job: the pack's D1 detector
(`proposals/scheduler._detect_title_question`) already refines guide-owned
questions silently from the second assistant turn and offers ephemeral
suggestions on user-owned ones — so the verbatim-first-message heading is
a one-turn transient, and the face's three-line heading clamp is the
turn-one display defense.

LLM drafting rides the SAME kind, flag-gated (`RECORD_LLM_DRAFTS=1`):
`llm_draft` prompts with a charter distilled from S6 (prose tracks
evidence; maturity named, never exceeded; negatives apart), then a
mechanical gate (`_gate_draft`) rejects lists, maturity-free prose,
id leaks, and INCOMPLETE prose (a draft cut at the token cap reads as a
truncated sentence on the face) whole — the deterministic composer
backstops every failure. The drafter also receives the claims'
image-bearing evidence (`_figures_of`, one-hop resolution exactly as
`supports_index`) and places `[[figure:id]]` markers at the point of
mention — the renderer embeds the figure there, mentioned figures leave
the trailing evidence strip, and the gate treats markers as markup:
stripped before the id-leak check, unknown ids dropped, one corrective
retry for the measured failure mode (weaving dilutes attention and the
maturity parentheticals drop).
Live content evaluation drove three charter iterations: the model first
added confirmation/success language, then invented a mechanism clause;
the shipped charter forbids BOTH by name ("include nothing the claims do
not state"), and the resulting drafts ground every clause in a claim.
Residual risk — ungrounded inference a regex cannot catch — is held by
the consent architecture: a draft is a proposal; the scientist reads
before it becomes record.

## Known gaps

- **Structure ops are substrate moves, not proposals yet.** Promotion/
  demotion happen by editing `parent_entity_id` directly; the class-3
  propose→ratify grammar for them (S6 charter) has no proposal kinds.
  Superseded prose has no face-side "show previous versions" door.
- **Distillation records are written by hand** (or future wrap flows); no
  advisor proposes closing a sitting with a distillation yet.
- **Trails, briefing, RFC** render only on the fixture face; the API does
  not carry them yet. Gestures v1 (investigation family + pin) ARE live;
  fade / ⌖ hold / drag-on-section remain fixture-only.
- **`open_questions` live in thread metadata as a blob** — concurrent
  writers (a running turn's title evolution vs. a face-side PATCH) are
  last-writer-wins. Small window, real hazard; a typed store is the
  eventual home. Project-level leftovers
  ship in the World but have no live organ either — the shelf renders
  per-session on the fixture face only.
- **Leftovers ignore proposal mentions** (§13.1 wants "no proposal/mention";
  v1 checks edges + pin only).
- **`whats_new` rides `log_event`, which is best-effort** — audit per-kind
  coverage before leaning on it (entity-model known gap). The per-user
  cursor is client-held (localStorage), by design.
