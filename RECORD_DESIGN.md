# The Record — a living lab notebook

*Design document for the altui2 concept. The interactive mockups are the
argument; this is the argument in prose. Prototype: `frontend/` —
`/notebook.html` (one mature moment) and `/workflow.html` (fifteen scenes
of a project's life). Build notes: `ALTUI2.md`. Study log and the
critiques that shaped each revision: `../NOTES.md`.*

---

## 1 · Premise

AI-assisted analysis tools make the **conversation** the primary surface:
the project's actual state — what is known, what is believed, what was
tried and failed — lives implicitly in a scroll of chat turns and a pile
of artifacts, reconstructed in the scientist's head on every visit. The
Record inverts this. The project's primary surface is a **single
co-written document over the entity graph**: a living lab notebook that
the scientist and the agent (Guide) write together, with the chat demoted
from destination to instrument.

The governing sentence of the whole design:

> **The document is where you stand; sessions are where you reach.**

You orient, decide, and resume from the document. You act by opening a
working session *over* it — scoped by where you summoned it — and what the
session produces flows back into the document through an explicit
ratification grammar. On any given day the scientist should be able to
answer "what do we know? what needs me? what is running? where was I?"
from one surface, in one glance, without replaying any conversation.

## 2 · First principles

These invariants hold at every scale and on every face of the Record.
Each earns its keep somewhere concrete in the mockups.

1. **Everything rendered is a live view over an entity — never pasted
   text.** Claims, figures, runs, trails, arcs appear in prose as
   reference chips resolving into the graph; a figure embed carries its
   provenance behind one click. There is no copy of a result that can go
   stale, and pointing at a thing is always possible because the thing
   has an identity.

2. **Noticed ≠ believed.** The document is stratified by epistemic
   status, not by document section. What we *conclude* (ratified
   narrative), what we've *noticed* (field notes, trails), and what
   *happened* (the sediment of runs) are different kinds of statement
   and must not blur. A hunch never has to masquerade as a finding to
   get written down.

3. **The agent proposes; only the user writes.** Ratified prose is
   immutable: the agent never rewrites it, it appends dated **addendum
   proposals** (Ratify / Dismiss / discuss). Updates append; syntheses
   supersede and archive; dismissals are remembered. "Evidence is mixed"
   is legitimate, permanent prose. The record is therefore always *the
   user's* record, with the agent's labor visible in signatures
   ("drafted by Guide · ratified by you").

4. **The prolific/rare asymmetry is structural.** Machines produce
   prolifically; insight is rare — and the geometry must encode that
   rather than fight it. A 104-output QC sweep is ONE sediment line;
   expanding it shows the 3 flagged panels and an honest note ("+101
   more — none demanded reading; nothing was lost"). Narrative
   growth-rate ≈ insight rate, and both are low; that is correct, not a
   failure of the tool.

5. **Structure is born from work, never from templates.** Day 0 is a
   composer, not a document with empty sections. Questions are born
   mid-conversation as stubs; trails open when something is noticed
   twice; arcs crystallize when questions demonstrably move together;
   the project abstract appears at the first consolidation event. The
   Record never renders scaffolding for what it does not have — a
   nearly-empty story is an honest face, not a broken one.

6. **Honesty about neglect and absence.** "3 drafts waiting — the record
   is ~4 days behind the work" is printed on the page. Dormancy, staleness,
   and unexamined leftovers are visible, recoverable states — never
   silent rot.

7. **Attention surfaces are derived, never declared.** Every count,
   badge, and tray over "what needs you" is computed from record state
   (pending addenda, draft fragments, unratified notes), so the number
   on the band, the rows in the tray, and the amber marks on the page
   agree *by construction*. There is no hand-kept inbox to drift out of
   sync. This discipline turns out to be what makes the attention
   system survive scale and restructuring untouched (§9, §10).

8. **The page never jumps.** The viewport holds a visible landmark
   steady (scroll-anchoring on an element near the top/middle of view).
   Updates landing *in* view materialize where they land — best seen,
   not suppressed; updates landing *out* of view go to the periphery
   (§6). The document may change before your eyes; it may not move
   under your hand.

9. **Everything is findable by every axis you might remember it on:**
   by entity (the claim, the figure), by place (the question it lives
   under), by time (the work record), by episode (the session, at turn
   grain), and by *what was said* (transcript search) — because
   episodic memory ("I remember discussing this in that sitting") is a
   first-class entry path, not an afterthought.

## 3 · Anatomy of the page

One scrollable document, three strata plus a periphery:

- **What's new** — the delta since last visit, one strip at the top.
  Loud items (⚡ conditions) and live items (▶ running) surface in the
  collapsed face; every item is a **door** that jumps to its subject.
- **The story so far** — the ratified narrative, organized per question.
  Sparse, load-bearing serif prose with live reference chips; every
  paragraph signed and dated; contradictions arrive as addendum
  proposals below the prose they qualify. Per-section **methods mode**
  expands every referenced result into its provenance-generated methods
  line — generated, never hand-maintained.
- **Field notes & trails** — the noticed-not-believed stratum. **Trails**
  are named hunches accumulating dated fragments over weeks, including
  counter-examples (that is what makes them honest); a cohering trail
  gets the agent's **coherence nudge** ("three consistent fragments —
  draft a claim?") whose accept lands a *draft* in the inbox, not prose
  in the record. Loose notes point at their evidence and get a weekly
  file-or-fade sweep.
- **Sediment — the work record** — every run, one line, automatic:
  date · state (▶ / · / ✗) · verdict · output count · trail/session
  marks · retention. Append-only, chronological, kept by the machine.
  Two grains: **by run** (flat chronology) and **by session**
  (episodes — see §5).
- **Periphery** — the TOC rail with position tracking and pulse badges,
  the delta rail (a minimap of change), the triage band (§8), and the
  ⌘K omnibox.

The same machinery renders alternate faces on demand — the **one-pager**
(Data · Method · THE number · Caveat · sediment appendix) for the
p-value visitor, and the **weekly digest** (§8) for the PI. No modes, no
imposed ceremony: thin projects render thin.

## 4 · The entity model underneath

The Record is a *rendering* over a typed graph; the strata are epistemic
levels of the same entities, and movement between strata is promotion,
gated by ratification.

| entity | lives in | states |
|---|---|---|
| **run / result / figure** | sediment (+ embeds anywhere) | running · ok · failed; retention: kept / temporary / at-risk |
| **note** | field notes | draft (agent-proposed) · filed · faded |
| **trail** + fragments | field notes | accumulating · cohering · stalled (folds) |
| **claim** | story (via chips) | ○ conjecture · ◐ supported · ◕ cross-checked · ● robust · ◮ contested |
| **question** (section) | story | stub · active · dormant (holds a claim) · closed · dead (epitaph) |
| **addendum** | story | proposed · ratified · dismissed (remembered) |
| **session** | everywhere it touched | open · parked · filed (filed ≠ dead) |
| **arc** | spine (§9) | open · closed — a *view over* questions, not a container |
| **synthesis** (abstract) | spine | current · superseded (archived, still cited) |

The promotion path runs upward only through explicit acts: sediment →
noticed (a note or fragment, drafted by either party) → trail coherence →
claim draft in the inbox → ratified narrative. The downward path exists
too and is just as important: questions go dormant holding their best
claim, die into epitaphs (§9), and nothing is ever deleted — faces
change, entities persist.

Pinning survives in spirit as *pointing*: any element can be held (⌖),
asked about (margin bench), made the conversation's subject (deixis, §6),
or promoted via a draft. What replaced the pin *button* is the rule that
the agent drafts placement continuously during work and the user ratifies
placement rather than performing it.

## 5 · The work loop — sessions

Where the actual work happens, and how it is tracked from the first
second:

- **A session is summoned from where you stand**, and its scope is its
  birthplace: opened from a question, it starts knowing the question,
  its evidence, both fits, and its trails — zero context-setting. The
  **margin bench** is the same instrument at its narrowest (one
  element); a project-scoped session is the same instrument at its
  widest. Scope chips at the panel head show exactly what the agent
  already has.
- **Runs land in the sediment at launch**, not after curation — the
  document records actions as they happen, marked with the session that
  produced them. Results return *into* the conversation; fragments and
  notes are drafted into the strata *during* the session, wearing
  visible `draft` badges.
- **Session close is a distillation moment, not an exit.** The panel
  proposes what enters the record — fragment → trail, addendum draft →
  question, keep/lapse → retention — and nothing enters without
  ratification. The transcript files under its anchor.
- **Sessions are first-class, because the redux is a map and sessions
  are the territory.** Curation is lossy by design, so the episode must
  stay reachable: the work record's **by-session grain** shows each
  sitting as a super-row (turns · runs · distilled · **unexamined
  count**) with its runs nested; a session renders as a **full page**
  (for sifting: distillate up top, the **leftovers shelf** — artifacts
  produced but never pinned, noted, or discussed, kept findable for
  late review — the transcript with **addressable turns**, chain edges
  `continues ← / continued by →`) or as a **docked panel** (side-by-side
  working mode), each converting to the other (⤢ / ⇥). Both end in a
  live composer: filed ≠ dead.
- **Turn-grade links everywhere:** sediment lines jump to the turn that
  launched them; trail fragments carry "▷ turn N" (provenance for
  prose); search covers what was SAID, landing on the turn highlighted.

## 6 · Live anchoring — the document as a working surface

With the Record on screen and a session docked at the right margin, the
document must track the work without wrestling the reader (rule 8):

- **Standing anchor state.** The live session's home section wears
  "▶ winter dig · working here" until close — scroll away and back, the
  hot region is unmistakable.
- **Changes land by visibility.** In view → materialize in place with a
  flash (the viewport scroll-anchors; the page never jumps). Out of
  view → a **TOC pulse badge** and a **delta-rail tick** (minimap
  idiom). Background events (a pipeline landing mid-session) use the
  same periphery; conditions additionally hit the what's-new strip —
  the one always-visible surface.
- **Exactly three signal tiers, with a lifecycle:**
  - **teal accretion** — routine landings; *clears once its region has
    been seen* (scroll-through marks it), so routine ticks never become
    wallpaper;
  - **amber awaiting-you** — persists until acted on, and is *derived*
    from pending state (rule 7);
  - **red condition** — contradictions, failures; persists until
    resolved.
- **Mutual deixis.** Click any element on the page and the panel's
  "looking at:" follows — pointing replaces context-setting. Agent
  messages point back ("show T1 on the page →" locates and flashes it).
- **The impact set.** The panel lists where the session has landed
  things ("touched: Q1 · T1 · sediment ×3"); at close, that list *is*
  what the distillation reviews.
- **Cross-boundary relevance stays a proposal** ("may bear on Q2 — file
  a note?"). The agent never writes outside the session's anchor
  silently.
- **hold ⌖** parks an excerpt on the desk for two-locus work; clears at
  session close.

## 7 · Glyph grammar

Uniform, small, and closed — every mark means one thing everywhere:

- **▷ / ▶** — THE session/execution marker; the arrow shape is the
  domain, fill/color is liveness (▷ outline teal = at rest, ▶ filled
  green = live now; runs' own state marks rhyme with it).
- **⚡** condition (contradiction, failure) · **⌖** held excerpt ·
  **`draft`** agent-proposed, awaiting ratification · **†** epitaph
  (dead line) · **⋱** trail · **○◐◕●◮** claim maturity.

## 8 · Attention and triage — the busy scientist

The Record is read far more often than it is worked. The five-minute
visit is a first-class use case:

- **The triage band** at the top of the document answers the whole visit
  in one glance: ⚡ conditions · ▢ N need you · ▶ running · ▷ resume ·
  ⌖ held. Every slot is a door; empty slots don't render.
- **The needs-you tray** (▢ opens it) lists everything pending —
  derived, so the count and the contents agree by construction. Rows
  carry their verbs: **Ratify** (decisions), **file ✓** (routine),
  **go →** (see it in context first). Acting in the tray and acting in
  place are the same state.
- **Tiered consent.** Routine items (notes, fragments) are veto-tier:
  batchable ("file all routine (N)") and undoable. Decisions (addenda,
  claim drafts) stay one-by-one. The scientist's ratification attention
  is spent where it matters.
- **⌘K omnibox** — ask or find from anywhere; hits span narrative,
  notes, sediment, epitaphs, and transcripts. The day-0 lesson ("the
  composer is the whole interface") made permanent.
- **"This week ▸" digest** — auto-rendered, emailable: conditions ·
  needs-you · new events · figure of the week. The consumption format
  for the projects a PI doesn't open daily.

## 9 · Scale — the Record recurses

The honest arithmetic: a Science-scale paper is 4–6 main plus 30–50
supplementary figures, times the 5–10× that never leaves the lab
(negative results, alternative attempts) — so ONE paper's project is
hundreds of figure-grade artifacts across 15–30 investigation lines over
its lifetime. A flat scroll comfortably holds **5–8 live narrative
lines**: one question's active working set — a *tenth* of one paper. The
single page cannot be the whole geometry, and the answer is one move:

**The Record recurses.** A mature project is a **spine** over **question
pages**:

- **The spine** is the project-grain face: a **rolling ratified
  abstract** over **arcs** (the aims/result-lines of the paper — views
  over questions, not containers), with every question as ONE line
  whose face follows its state:
  - *open* — a "now" sentence, session chip, live badges, `open ▸`;
  - *held* — the claim it sleeps on + `wake ▸`;
  - *closed* — the ratified verdict, reading like a published abstract
    line;
  - *dead* — an **epitaph**: hypothesis · verdict · the run that killed
    it · date. One line forever, searchable as its own stratum. The
    paper reports the survivors; the record keeps the casualties —
    "did we ever try X?" answers in one query, years later, with the
    killing run attached. Institutional memory of negatives is the
    Record's strongest claim over a paper, made structural.
- **Consolidation is a ratification event.** Each synthesis *supersedes*
  the last, which archives beneath it — immutable, still cited. "The
  story so far" literally means so far; the narrative does not grow
  monotonically for three years.
- **Compaction is the common case, not the edge case.** A mature spine
  is a table of contents with three chapters open. Every element has a
  **disclosure ladder** — line → abstract → full — with the default
  face chosen by state and any face pinnable. Folded arcs show counts
  *and what the chapter holds* (the fold is an abstract, not a blank);
  stalled trails fold; the sediment shows a recent window over a
  declared archive ("all 1,847 runs, searchable").
- **The periphery rolls up the tree.** An arc's badge aggregates its
  children's deltas, same three tiers. The triage band, tray, and
  digest survive at any scale *untouched* — they were always derived
  from record state, never from page position.
- **Descend, and the whole notebook is there.** A question page (behind
  `open ▸`, under a breadcrumb `‹`) IS the full single-scroll face —
  narrative as the question's sub-lines, its trails, its sediment
  slice, its sessions. Nothing is redesigned at depth; the flat Record
  of the early mockups *was the question-grain face all along*. Depth
  follows the science — program → paper/arc → question — with no fixed
  cap.

## 10 · Maturation — how structure appears

There is no restructuring event, because **page structure is a
rendering, not a container**: every face is a projection over the same
graph, and every link, badge, and search hit addresses entities, not
page positions. Maturation is a gradient of default-face changes —
mostly automatic, punctuated by a few ratified promotions. The triggers
are **content pressure, never calendar age**:

- **Stage 0–1 · one page, everything full-face.** Day 0 is a composer;
  the document builds itself from work (questions born mid-conversation
  as stubs; strata appear when they first have content).
- **Stage 2 · in-page compaction — automatic.** Dormant questions
  collapse to their holds-line, stalled trails fold, the sediment
  windows itself. Face-flips driven by state, reversible by a click,
  veto-tier: nothing is being decided, the content is one click away.
- **Stage 3 · a question outgrows the page — the first ratified
  promotion, per-question.** Trigger: the section's own working set can
  no longer be shown without compacting *live* material. The agent
  proposes it like any addendum ("Q1 has grown past the page — give it
  its own page and hold it here as one line?"); ratifying flips the
  rendering. Nothing moves in the graph; nothing breaks. The resulting
  **hybrid page — some questions inline at full face, some descended to
  one-liners — is a legitimate long-lived stage**, not a transitional
  embarrassment; the spine is simply its limit once every question has
  descended.
- **Stage 4 · arcs crystallize.** When the question list grows long
  (~10+ lines) and stable groupings are visible in the graph itself
  (co-cited claims, shared trails, co-anchored sessions), the agent
  proposes the grouping; the user names it — naming is the
  ratification. Because arcs are views, a question can later migrate
  between arcs without anything breaking.
- **Stage 5 · the abstract appears** at the first consolidation event —
  the first arc closes, or the one-pager is first needed for a meeting.
  Drafted by the agent from the per-question holds lines, ratified by
  the user, and thereafter on the supersede-and-archive cycle.
- **The ladder runs down too.** A dead question's spine line becomes an
  epitaph; its page stays reachable behind it. De-maturation is faces
  flipping back.

Two properties make this gradualism safe: the attention system is
derived (the "needs you" count is identical the day before and after a
question descends), and addressing is by entity (nothing 404s because
the furniture moved).

## 11 · One system, two renderings

The Record (altui2) and the Board (altui1, "Bench & Board") were built
as competing concepts, but every stress test grew the same organs in
both: a pending-decisions projection, a conditions row, a vitals band, a
triage/inbox surface, dormant compaction, a digest. The conclusion is
recorded in `../NOTES.md`: the two prototypes differ in **page geometry
and emphasis** (document-first narrative vs. board-first cards) over
**one shared accretion pipeline**, and in a real build the organs should
be shared components with only the rendering differing. The Record is
therefore best read not as a replacement UI but as a candidate *primary
face* for the same underlying ABA entity system.

## 12 · What the storyboard demonstrates

Fifteen interactive scenes, one parameterized renderer
(`/workflow.html`, arrow keys / pills / `?step=` deep links; some scenes
advance through their own affordances — typing in the day-0 composer,
`work ▸`, `file & close`, `open ▸` on a spine line):

- **I · Early days (E1–E5)** — the hard, nothing-to-anchor-on case: a
  new project is a composer, not a document; the first exchange births
  the sediment; noticing becomes notes; the first question is born
  mid-conversation; day 3 reads as a lab diary.
- **II · Mature (M1–M8)** — re-entry and orientation; a session opened
  from a question with scope in hand; the churn loop with live
  accretion; session close as distillation; the morning after (the
  by-session work record); the session page (territory behind the map);
  live anchoring; year 2 — in-page compaction and the triage surfaces.
- **III · Very mature (M9–M10)** — the spine: recursion, arcs,
  epitaphs, rolled-up periphery; descend into one question and find the
  whole earlier prototype there.

## 13 · Coexistence — the Record as a face over live ABA projects

*Grounded in a read-only pass over the main repo (2026-07): `docs/arch/`
+ targeted reads of `core/graph/`, `content/bio/entity_types/`,
`guide.py`. File references below are into `aba/backend/`.*

The strategic question: can the Record run as an **alternative UI layer
over the same projects** driven by the existing workspace UI (or its
successor) — so that scientists can try different faces, on real
projects, and the winning UX can be discovered rather than guessed? The
substrate audit says **yes, and more cheaply than expected**: most of
what the Record renders already exists in the waist, the platform's
extension points are exactly the ones needed, and the three invariants
coexistence requires are *already enforced platform properties*.

### 13.1 What the substrate already provides

| Record concept | Substrate reality |
|---|---|
| additive ontology | new entity types are YAML registrations in the content pack, "no core edit, no column, no migration" (`core/entity_types/registry.py`; `content/bio/entity_types/`) |
| questions | **threads are questions already**: `thread.yaml` carries `title` + `question` + `open_questions` + lifecycle `open / parked / concluded` (+ `conclude_wrap`, `question_source`). open → active section · parked → held · concluded → closed |
| claim maturity | `claim.yaml` `confidence_model`: preliminary / supported / validated / contested / **refuted** (terminal), with a `status_log` audit trail — maps nearly 1:1 onto ○◐●◮, and *refuted* feeds epitaphs |
| prose, notes | `narrative` (long-form prose as first-class entity) and `note` (incl. the `keep_message` gesture — pin a chat message as a note) already exist |
| needs-you tray | a **`proposals` table + store** exists: `status='pending'`, accept / dismiss / **undo** plumbing, thread-scoped, per-role — with signature dedup whose docstring states the Record's rule verbatim: *"a dismissed idea doesn't re-nag until the world changes (which yields a new signature)"* (`core/graph/proposals_store.py`) |
| agent drafting | the **advisors framework** is the proposal-writer pattern already running: skeptic (fires when a claim's evidence weakens), methodologist (confidence transitions), stylist (prose nudges) — YAML-spec'd roles feeding the proposals table (`content/bio/advisors/`) |
| what's-new | an append-only **`events` table** with `entity_id` per event (`core/graph/audit.py: log_event / list_events`) — the doors come for free |
| turn-grade provenance | every entity carries `actor` (`agent:<run_id>`) + `derivation(exec_id)` — **enforced ~100% by CI ratchet + backfill**; `execution_records` rows carry `thread_id / run_id / tool_use_id`; `runs` rows carry `thread_id / session_id / turn_index`; messages are ordered rows per thread. The sediment line → "▷ turn N" jump is a **join, not new capture** |
| downward disclosure | `GET /api/entities/{id}/provenance` already assembles method / inputs / environment / attribution / lineage from the exec sidecar (`core/graph/provenance_evidence.py`) — the "how was this made?" drawer's exact backend |
| leftovers shelf | computable as the edge-complement: artifact entities with no `includes`/`supports` edges and no proposal/mention — the negative definition needs no new data |
| retention | promoted/registered vs. scratch-tier substrate (`core/data/store.py`) maps to kept vs. temporary |
| alternative faces | the Contact plane is registry-based (focus-views, viewers, labels) behind a typed API seam, with URL-canonical state; "multi-surface consistency falls out for free" is its own stated design goal (`docs/arch/contact-surface.md`) |
| organ-level telemetry | `tool_invocations` + `llm_generations` tables already measure usage; `proposals.status` transitions measure ratification behavior directly |

The three invariants coexistence requires turn out to already hold:
**single write path** (the UI reads/writes only through the model API —
lint-ratcheted), **shared pending store** (the proposals table *is*
cross-face by construction), **entry provenance** (derivation + actor
enforced at create, human vs. agent attribution recorded on every row).

### 13.2 The honest deltas

What the Record needs that the substrate does not have today, smallest
first:

1. **Render mappings, not schema**: maturity glyphs over the existing
   confidence ladder; epitaph = a concluded thread whose `conclude_wrap`
   carries a negative verdict + killing-run ref (additive metadata).
2. **New types by registration**: `trail` (+ fragments), later `arc`
   (a *view over* threads, per §10). YAML drop-ins.
3. **New proposal kinds** for record-writes — addendum, fragment
   placement, structure promotion (§10's descend/arc/abstract moments).
   The `plan` entity's present→approve gate is the existing template
   for exactly this propose→ratify shape.
4. **Prose ratification + supersede**: `narrative` entities exist, but
   revision machinery (`wasRevisionOf`, `set_current_revision`) is
   figure/table-only today (a known gap in `provenance.md`). Extending
   it to narrative gives the synthesis supersede-and-archive chain;
   ratification metadata (drafted-by / ratified-by / immutable-after)
   is additive.
5. **The sitting is not first-class** — the one real modeling delta.
   ABA's thread conflates the *question* with its *one continuous
   conversation*; the Record wants many bounded sittings per question
   (open → work → distill → file). The raw material exists (`messages.
   thread_id`, `runs.thread_id/session_id/turn_index`, exec records),
   so sittings are **derivable by clustering** for a first face, and a
   lightweight `sitting` entity (grouping run ids, holding the
   distillation record) is the eventual honest home. Notably the
   classic UI would benefit from the same episode structure on long
   threads — this delta is an improvement to the shared substrate, not
   a Record-private need.
6. **Events coverage + cursor**: `log_event` is best-effort by design
   (never raises) — audit per-kind coverage before trusting it as the
   what's-new source, and add a per-user last-visit cursor.
7. **Advisor roles for the accretion discipline**: drafting-during-work,
   distillation-at-close, trail-coherence — new advisor specs + skills
   on the existing framework, with drafting intensity a project-level
   setting so classic-UI users aren't flooded (their faces already have
   a Proposals surface to catch what does get drafted).

Nothing in this list is core surgery; items 1–3 and 6 are small, 4 and
7 are moderate content-pack work, 5 is the one genuine design decision.

### 13.3 Phased rollout — each phase independently useful

1. **Read-only Record face** over any existing project. A World-assembler
   service projects the graph into the shape the prototype already
   renders (`altui2 world.ts` is the draft response contract): sediment
   from exec records + jobs, work record by thread/turn, what's-new from
   events, story stubs from threads (question, open questions,
   lifecycle), claims with confidence, transcripts with turn jumps,
   leftovers by edge-complement, provenance drawer via the existing
   endpoint. Zero writes, zero new entities, works on every project on
   day one — and already tests the core orientation claim.
2. **Shared triage**: render the proposals table as the needs-you tray
   (accept/dismiss/undo already exist; batch-file = bulk accept of
   routine kinds), wire the last-visit cursor, audit event coverage.
   From here on, ratifying in the Record and ratifying in the classic
   UI are literally the same row.
3. **Authoring strata**: register `trail`; add the record-write proposal
   kinds; add the drafting/distillation advisor roles; extend revisions
   to narrative for ratified prose. Enable per-project.
4. **The spine**: arcs, the rolling synthesis, per-question descent —
   which by then is purely a rendering decision, since threads are
   already the question-grain unit.

### 13.4 The experiment this architecture buys

Because faces are renderings over one substrate, the UX question ("what
actually helps scientists?") becomes answerable **within-subject on live
projects**: a scientist flips workspace ⇄ Board ⇄ Record on the same
project mid-week, with no data forked and no cohort confound. Better,
instrumentation lands at the **organ** level, which is the granularity
that matters — not "Record vs. classic" but: do addenda get ratified,
and how fast (proposals.status transitions)? do trails accumulate or
rot? does the tray get used or bypassed? do session-page visits happen
(the leftovers bet)? Each organ earns or loses its place on evidence,
and the end state may well be a recombination of faces rather than a
winner — which the shared-organ architecture (§11) accommodates by
construction.

## 14 · Editorial governance — inertia, consent, and the scientist's hand

*Distilled 2026-07 from two external design reviews of the living-manuscript
problem (multi-year, agent-maintained scientific documents), read against
this design and the §13 substrate audit. Both reviews independently arrive
at the Record's architecture — a stable readable surface over an evidence
graph, structure changes as proposed editorial events. What they add, and
what this section absorbs, is the machinery AROUND structural change:
how often, how batched, how bounded the consent load, and what makes a
restructuring proposal trustworthy. Two worries drive it: the document
must keep coherently capturing the project as it scales — adding a level
of structure, splitting sections, relegating material to appendix rank —
without disorienting its owner; and the scientist must hold the emphasis
and direction of the presentation, from "this matters" down to the
sentence.*

The Record's standing answer to disorientation is structural: every face
is a rendering over the entity graph, so links target entities (nothing
404s when furniture moves) and attention is derived (the needs-you count
is identical the day before and after a question descends — §10). That
makes restructuring *safe*. This section is about making it *felt* as
safe.

### 14.1 Consent arithmetic — the queue is bounded by construction

The failure mode to design against is not a missing consent dialog; it is
consent *inflation*. Three structural proposals a week over a three-year
project is ~470 decisions; a queue that grows without bound trains
"accept all", which is worse than no consent at all because it launders
change as approved. The goal is to minimise consent events while keeping
control genuine. Every change the system can make falls in one of five
classes:

| class | examples | semantics |
|---|---|---|
| **0 · silent** | number/cross-ref/caption refresh from provenance | applied, logged, always revertible |
| **1 · notified** | in-slot prose where the claim is unchanged; figure re-render from updated data | applied, marked in place, one-click revert, appears in the briefing |
| **2 · proposed** | face flips, section split/merge within a question, tier moves within the page | batched to the tray; **expires to its default after ~14 days, visibly** — legitimate only because Class 2 is *defined* as local-and-reversible |
| **3 · consent** | cross-question moves, descend/arc/abstract promotions (§10), anything touching a pinned region or the synthesis | never auto, never expires, waits indefinitely |
| **X · interrupt** | the closed list: a contradiction between threads; evidence contradicting ratified prose (the addendum grammar, §3 of first principles); claim language exceeding its evidence | breaks the ambient rule; a fourth candidate gets argued down to Class 2, never added |

Class-2 expiry is the load-bearing rule: the tray cannot grow past ~14
days of routine plus whatever Class 3 the scientist is deliberately
sitting on. Two companions keep it honest. **Trust ratchets in both
directions** — after a run of accepted refreshes the system proposes
lowering its own ceremony; after rejections it raises ceremony *and says
so* (a system that visibly loses autonomy when wrong is one a scientist
will let act). And **rejection captures a reason that becomes a durable
rule** — filed through the proposals store's own discipline (signature
dedup: a dismissed idea doesn't re-nag until the world changes) and, when
the reason generalises, appended to the project charter as a standing
rule.

### 14.2 Structural inertia — hysteresis, disruption cost, budget

Restructure when the preference is **large and persistent**, never
because the optimiser flipped once:

```
propose(change)  iff  Δutility − λ·disruption > θ   for N consecutive cycles
```

- **disruption** is priced in reader-visible units: words moved, anchors
  and cross-refs rewritten, whether the scientist read or edited the
  affected region recently, whether human-authored spans are displaced.
- **λ grows** with project maturity (a month-38 thesis is nearly frozen)
  and with human edit density in the region — touched text is
  load-bearing.
- **N** (2–4 cycles) is a Schmitt trigger: one noisy result cannot flip
  the structure and the next flip it back.
- A **structural budget** caps reader-visible reorganisation per cycle
  regardless of how much the optimiser wants.

Structure proposals batch into one **restructuring proposal** reviewed in
one sitting, each item carrying the *why in evidence terms* ("F-0441
supersedes F-0217; local 2.6σ → 1.1σ"), the *reader impact quantified*
("~2,100 words move; 11 cross-refs rewritten"), the *alternative
considered and why rejected*, and granular verbs — accept / accept
partially / not yet / **never** (which writes the rule, §14.1).

**The shadow recompile** decouples exploring better structure from paying
for it: periodically derive the blank-slate organisation from the current
graph with zero inertia, never publish it, diff it against the live
Record — "built from scratch, this project would organise by method
rather than channel; three sections differ · view · adopt partially ·
dismiss for 6 months." It is the one component immune to the project's
own path-dependence, and the anti-anchoring valve for a face that has
been accreting for years.

### 14.3 Write once at several depths; place freely

The precondition for cheap inertia is that **demotion must not be a
rewrite**. When prose is ratified, the draft carries its renditions
together — headline (the spine one-liner), abstract paragraph (the fold /
holds-line), full exposition — ratified as one act. Every later face
flip — compaction, descent to a one-liner, appendix relegation, the
"technical footnote" ending — is then a *selection*, not new writing: it
needs no fresh wording consent and is losslessly reversible. Figures are
recipes, `render(finding, tier)`, never files, so a hero panel and a
thumbnail in a validation grid are parameter values. This is what §10's
gradualism quietly assumed; stated as a contract: **a tier operation
never generates prose, it selects among renditions the scientist already
ratified.**

### 14.4 Emphasis is a signal — salience with evidence floors

Between the charter and the sentence there must be an instrument for
"this is important — lead with it." Placement is driven by an explicit
function — terms for **scientist interest** (the human-set emphasis
signal), evidence strength, **narrative necessity** computed from the
dependency graph, novelty, and effort-invested (weighted low, shown
openly, so that argument happens explicitly rather than through repeated
manual re-promotion). Two guards make it trustworthy:

- **An evidence floor per tier.** Nothing reaches the top of the story on
  enthusiasm alone — and the refusal is legible, in evidence terms:
  *"raising your interest weight would not change the outcome; the lead
  position requires cross-checked (◕) or better."* The anti-sycophancy
  mechanism.
- **Necessity is computed, not felt.** The boring calibration the main
  result depends on cannot sink below supporting rank — what stops the
  Record becoming a highlight reel.

The arithmetic stays internal. The surface shows **support states and
sentences, never floats** — the maturity glyphs (○◐◕●◮), roles, and
one-line explanations on hover ("why this placement: 2 claims, held by
Q1, last activity 3w"), extending the standing rule that every derived
thing is explainable in place.

Emphasis is *expressed* through content gestures, not settings: dragging
a figure to the front of a section is the physicist's native
prioritisation act. The chain is **gesture → inferred intent →
consequence → confirm**: "You moved the ROC curve to the lead. I read
that as promoting the classifier cluster. That would expand its section
L1→L2, re-render 2 figures, and demote nothing. [yes] [just the figure]
[show diff]." A **figure board** (tier rows with visible slot scarcity;
dragging into a full tier forces an explicit eviction) makes the
prioritisation conversation physical — and its **"not shown" pile is
deliberately visible and deliberately uncomfortable**: that count
climbing is the user-facing form of the narrowing pathology (negative
results and abandoned threads quietly vanishing from every view).

### 14.5 Charges — the scientist's direction, durable

Each question section (and later each arc) carries a **charge**: two or
three ratified sentences of editorial intent — *"present the excess as it
appeared, the checks, and its disappearance; frame as methodological
lesson, not as a result"* — plus a length budget and must-not-duplicate
edges. Writers draft against the charge; a critic checks compliance;
boundary smear between sections becomes a lintable defect instead of a
slow fate. The charge is edited **in place** — click the section head and
its governing metadata unfolds (charge · tier · budget · authorship split
· pinned state). There is no separate plan surface: the spine *is* the
map, and pinning stays the one-click veto.

Around the charges, a **proactivity gradient by region** — automation
spent where identity isn't:

| region | default |
|---|---|
| numbers, cross-refs, captions, bibliography | act silently (Class 0) |
| in-slot prose, claim unchanged | act + notify (Class 1) |
| prose where the claim changed | propose |
| structure, tiering, promotion/demotion | propose, batched (§14.2) |
| synthesis, abstract, title, framing | **never unbidden** |

And one physics-culture jewel to build early: the **evidence-to-language
lint**. Prose asserting a rhetorical band stronger than its bound claim's
maturity permits fails at the ratification gate — *"'evidence for'
requires ◕ cross-checked; this claim is ◐ supported. Permitted here: 'a
mild excess', 'a fluctuation'"* — with override-plus-note for the
legitimate exception. It is the mechanism by which a continuously
maintained document becomes *more* trustworthy than a hand-written one.

### 14.6 Re-entry and absence

For the returning scientist the delta strip is the wrong grain. Past a
few days away, orientation is a **briefing**, not a diff: authored prose,
past tense, ranked by consequence, scaled to time away, leading with what
changed *in the science* — and flagging what the system could not resolve
("you wrote that the excess is robust to calibration; F-0441 contradicts
this; I left your sentence alone"). Never activity volume. Archived
briefings accumulate into the project's own narrative history — the
material a good concluding chapter is made of, otherwise always lost.

The **absence policy** is the two-clock discipline applied to attention:
while the scientist is away the fast clock runs (numbers, figures,
sediment stay current) but nothing above Class 1 applies, and **Class-2
expiry timers pause**. You return to a document that is factually current
and structurally exactly as you left it, plus a finite stack of held
proposals.

### 14.7 Where this lands on the substrate

Nothing here disturbs §13's audit; it arrives with rollout phases 3–4 as:
additive entity metadata (salience terms, multi-depth renditions, charges
on threads), new proposal kinds (restructuring items, rendition
selections), advisor roles (historian/briefing, shadow-recompile,
narrowing-watch), charter rules in the existing scoped rules bundles
(rejection-derived rules included), and one check at the ratification
gate (the language lint). The health metrics are organ-grade, per §13.4:
structural accept:reject in the 70–85% collaborator band, proposal
reversal rate near zero (the thrash detector), consent decisions per
month *falling* as the ratchet works, time-to-orient after ≥7 days away
under two minutes, pin count trend (rising pins = the scientist defending
territory against the salience model), and negative-result coverage (its
decay to zero is the narrowing pathology arriving).

## 15 · Deliberate scope cuts and open questions

Cut from the prototype (mimed, not wired): real chat; ratification and
draft state beyond the client; prose editing (the co-writing loop);
backend/graph persistence.

Open design questions, in rough priority order:

1. **Periphery density** under 3+ concurrent background pipelines —
   tiering + accretion fade should carry it; a per-anchor mute is the
   pressure valve if not.
2. **The promotion moment on stage** — the storyboard shows the stages
   of §10 but not the hinge itself (the agent's descend proposal and
   its ratify); a scene between M8 and M9 would carry it.
3. **Epitaph pages** — a dead line still owns trails and sediment;
   likely `page ▸` on hover, rendering the standard question page in a
   closed state.
4. **Auto-assembled abstract** — should the spine abstract assemble
   from per-arc holds automatically, with the user promoting it to
   ratified prose (same nudge grammar as claims)?
5. **Cross-project home** — the five-project PI's landing surface
   (digests as cards?); out of scope for this worktree.
6. **Tray keyboard nav** (j/k + enter) and digest email delivery —
   mechanical, deferred.
7. **Governance constants** (§14) — the Class-2 expiry window, the
   hysteresis trigger N, and the structural budget are asserted, not
   calibrated; instrument proposal rates and reversal rates from the
   first live phase and tune against the 70–85% accept band.
8. **Where charges live** (§14.5) — extend `thread` metadata
   (question/conclude_wrap already carry intent) vs. a small `charge`
   entity; leaning to thread metadata, decide at phase 3.
