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

## Deliberate scope cuts

Chat is canned; drafts are fixture data; ratification/nudge state is
client-local; no editing of prose (the co-writing loop is mimed, not wired).
Geometry and feel are what's being tested — per NOTES.md, the real fork
between altui1 and altui2 is rendering emphasis over one shared accretion
pipeline.

Design notes and study synthesis: ../NOTES.md (repo-external).
