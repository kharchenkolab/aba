# altui2 — The Record (living lab notebook prototype)

Prototype of `misc/alt_uis.md` §2 — the project's primary surface as a single
co-written, **stratified document**. A clean-slate concept: it deliberately
inherits nothing from the workspace chrome (paper ground, serif narrative,
document geometry), only the entity-graph *ideas*.

## Run

```
cd frontend && npm ci && npm run dev    # then open /notebook.html
```

Fully client-side: one fixture module (`src/notebook/fixture.ts`, the same
generic *Coastal sensor study* world as altui1) + static plot assets. No
backend, no proxy. The original workspace app is untouched in this branch
(restore its dev proxy with `ABA_PROXY=1` if you want to run it).

## The three strata (§2.2), as built

- **The story so far** — ratified narrative per question. Prose carries live
  inline references (`[[claim:…]]`, `[[fig:…]]`, `[[trail:…]]`, `[[run:…]]`)
  rendered as chips/embeds over the graph — never pasted text. Every
  paragraph is signed *drafted by Guide · ratified by you · date*. The agent
  never rewrites ratified prose: the winter contradiction arrives as a dated
  **addendum proposal** (Ratify / Dismiss / discuss); ratifying turns it into
  a quiet dated addendum. "Evidence is mixed" is legitimate, permanent prose.
- **Field notes & trails** — noticed ≠ believed. **Trails** are named hunches
  accumulating dated fragments across weeks (incl. a red-marked
  counter-example); the cohering trail carries the agent's **coherence
  nudge** ("three consistent fragments — draft a claim?") whose accept lands
  a draft in the inbox, not the record. Loose notes get the file-or-fade
  sweep note. Guide-drafted notes *point* (each cites its figure).
- **Sediment** — every run, one line, automatic: date · state (▶/·/✗) ·
  verdict · output count · trail ref · retention (kept ✓ on hpc / temporary /
  at-risk). The 104-output QC sweep is ONE line; expanding shows only the 3
  flagged plots + a summary ("+100 more — none demanded reading; nothing was
  lost"). The prolific/rare asymmetry, structurally.

## The interaction contracts (§2.3–2.4)

- **Downward disclosure**: any figure → *how was this made?* → producing run
  · duration · placement · inputs, with code / params / env / log tabs.
- **Margin bench**: *ask ✦* on any element opens a transient right-margin
  chat anchored to that element — zero context-setting (the focus contract,
  repointed at the document). Canned exchanges in the prototype.
- **Methods mode** per section: every referenced result expands into its
  provenance-generated methods line inline.
- **What's new** strip: delta since last visit with the contradiction loud
  and the live run pulsing, plus the honesty line ("3 drafts waiting — the
  record is ~4 days behind the work") — neglect is visible, never silent.
- **Three-scope search**: story / noticed / everything, scoped to the strata.
- **Focus spectrum**: *view as one-pager* renders the p-value visitor's
  deliverable (Data · Method · THE number · Caveat · sediment appendix) from
  the same machinery — no modes, no imposed ceremony.

## The work loop (PK: "where is the actual work done and tracked?")

The Record as first prototyped rendered only the RESIDUE of work (sediment /
notes / narrative are all downstream); the daily loop was invisible. The
answer, made concrete in the **workflow storyboard** (`/workflow.html`):
*the document is where you stand; sessions are where you reach.*

- **Working sessions** are the chat, demoted from destination to instrument:
  a panel opened OVER the document, scoped by where you summon it (project /
  question / trail / figure — same instrument as the margin bench, wider
  scope). Opened from Q1 it starts knowing the question, its evidence and
  trails — zero context-setting.
- **Runs land in the sediment at launch** (▶ line, marked ▷/▶ with their
  session) — the document records actions as they happen; results return
  into the conversation; fragments/notes are drafted into the strata DURING
  the session (visible `draft` badges).
- **Session close is a distillation moment**: the panel proposes what enters
  the record (fragment → trail, addendum draft → question, keeps →
  retention); nothing enters without ratification; the transcript files
  under its anchor — reachable from the section head (▷), the desk, and
  every sediment line it produced. Work is findable from what it touched.
- **The desk strip** is the document's present tense: open sessions, running
  work, yesterday's resume point.

**Sessions surfaced (PK: "the real analysis is in sessions, Runs and
Results").** The redux is a MAP; sessions are the territory — they hold
what curation missed (unpinned artifacts, asides) and they match episodic
memory (you remember the sitting, not the entity). So sessions are
first-class, not scaffolding:

- **The work record has two grains**: *by run* (flat chronology) or *by
  session* — each sitting one super-row (turns · runs · distilled ·
  **unexamined count**) with its runs nested; solo/automatic runs stand
  apart. The session is the chain; sometimes the chain is what you follow.
- **A session is a full page AND a docked panel** (⤢ / ⇥ convert): the
  page for sifting — distillate up top, the **leftovers shelf** (artifacts
  produced but never pinned, noted, or discussed — kept findable for late
  review), the transcript with **addressable turns**, chain edges
  (continues ← / continued by →); the docked panel for side-by-side work
  (the existing chat-in-right-column mode). Both end in a live composer:
  filed ≠ dead.
- **Turn-grade session links everywhere**: sediment lines jump to the turn that
  launched their run; trail fragments carry "▷ turn N" (provenance for
  prose); search covers **what was SAID**, not just what was kept — a
  transcript hit lands on its turn, highlighted.

**Live anchoring (PK: how does the Record track the work as it happens?).**
The governing rule: *the document may glow anywhere, but it moves only
under the user's hand* — no auto-scroll, ever. Mechanisms (scene M7):

- **Standing anchor state**: the live session's home section wears
  "▶ winter dig · working here" until close — scroll away and back, the
  hot region is unmistakable.
- **Changes land by visibility**: in view → materialize in place with a
  flash; out of view → a **TOC pulse badge** and a **delta-rail tick**
  (minimap idiom), three tiers only: teal accretion · amber awaiting-you
  · red condition. Non-session events (a background hold-out landing) use
  the same periphery; conditions also hit the what's-new strip — the one
  always-visible surface — as a live ticker.
- **Mutual deixis**: click any element on the page → the panel's
  "looking at:" follows (pointing replaces context-setting); agent
  messages point back ("show T1 on the page →" locates and flashes it).
- **Impact set**: the panel lists where this session has landed things
  ("touched: Q1 · T1 · sediment ×3") — at close, that list IS what the
  distillation reviews.
- **Cross-boundary relevance stays a proposal** ("may bear on Q2 — file a
  note?"): the agent never writes outside the anchor silently.
- **hold ⌖**: pin an excerpt to the desk for two-locus work; clears at
  session close.

**Glyph grammar** (uniform; PK: one prominent arrow beats ⟲): the ARROW
is the session marker, state carried by fill/color — **▷** outline teal =
session at rest (filed/parked), **▶** filled green = live now. It rhymes
with runs' own ▶ state marks: the arrow family is the execution/session
domain, fill is liveness. **⚡** contradiction/condition · **⌖** held
excerpt · **draft** badge = agent-proposed, awaiting ratification.

**Busy-scientist surfaces** (OODA pass, see NOTES.md §11): the **triage
band** (⚡ conditions · ▢ needs-you · ▶ running · ▷ resume — one glance,
every slot a door); the **needs-you tray** (all pending decisions,
derived from record state so count and content agree by construction;
Ratify / file ✓ / go →; "file all routine" batches the veto-tier,
undoable); what's-new items as doors; badge lifecycle (accretion clears
on view, amber derived from pending, red until resolved); panel as
slide-over below 1000px; **⌘K omnibox** (ask or find, from anywhere);
**"this week ▸"** — the auto-rendered, emailable digest; and the scale
face — dormant questions compact to one line, stalled trails fold, the
sediment declares its recent window.

**The storyboard** (`frontend/src/workflow/`) plays this as thirteen
interactive scenes over the SAME Record renderer (parameterized over a
`World`, `src/notebook/world.ts` — `/notebook.html` is unchanged):
*Part I, early days (day 0–3)* — the hard, nothing-to-anchor-on case: a new
project is a composer, not a document (E1); the first exchange births the
sediment (E2); noticing becomes notes (E3); the first question is born
mid-conversation as a stub section (E4); day 3 reads as a lab diary (E5).
*Part II, mature (month 4)* — re-entry and orientation (M1); a session
opened from a question with scope in hand (M2); the churn loop with live
accretion (M3); walking away = distillation (M4); the morning after —
the by-session work record, resume from the document (M5); the session
page itself — territory behind the map (M6); live anchoring — the
document as a working surface (M7); year 2 — the scale face + triage (M8).
Some scenes advance through their own affordances (typing in the day-0
composer, `work ▸` on a question, `file & close` in the panel) — the story
moves the way the scientist would. Arrow keys / pills / `?step=` deep links.

## Deliberate scope cuts

Chat is canned; drafts are fixture data; ratification/nudge state is
client-local; no editing of prose (the co-writing loop is mimed, not wired).
Geometry and feel are what's being tested — per NOTES.md, the real fork
between altui1 and altui2 is rendering emphasis over one shared accretion
pipeline.

Design notes and study synthesis: ../NOTES.md (repo-external).
