# The reader rubric — content evaluation of a rendered Record page

Structural checks (DOM counts, overflow booleans, element presence) prove a
page *renders*. This rubric evaluates the property that matters: **a
scientist glances at the page and learns the state of their project.** It is
applied by READING the rendered page as that scientist — not by querying it.

Apply at every major change to the face, over the growth-arc stages
(`scripts/record_growth_demo.py`, stage1..stage6): a face that reads well at
one scale and degrades at another fails the arc, not the stage.

## The seven questions

Score each 0 (fails) / 1 (partial) / 2 (holds). Record the answer as a
sentence about THIS page, not a checkbox.

1. **Ten-second test.** Within ten seconds of looking, can you answer
   "what is this project and what have we learned?" — from rendered words,
   not from prior knowledge of the fixture.
2. **Narrative present.** Does the story stratum carry *prose* — readable
   sentences stating findings at their maturity — or only chrome around an
   absence (stubs, chips, link rows)? A page whose sections are all
   "Nothing ratified yet" past the project's first days scores 0.
3. **Signal over chrome.** Of the visible page, does signal (claims,
   verdicts, prose, open questions) outweigh chrome (navigation rows,
   repeated links, ids, empty-state text)? Count what the eye crosses
   between one finding and the next.
4. **No internal ids as reading matter.** Entity/run/sitting identifiers
   (`thr_…`, `sit-…`, `run_…`) may live in hover titles, doors, and the
   sediment table — never inline in the story stratum's sentences.
5. **Emphasis matches maturity.** The strongest-supported line of inquiry
   reads as the most prominent; dormant and refuted lines recede. If a
   glance ranks the questions differently than the evidence does, fail.
6. **Change is findable.** Can you spot what happened since the last visit
   (what's-new, tray) without scanning every section?
7. **Density grows with content, not with rows.** Compare against the
   previous stage: did the page gain *information* (new findings, matured
   claims, richer prose) or only *length* (more identical rows)? Repeated
   near-identical lines are sediment and belong below the fold.

## The interaction half (added after the first hands-on user test)

Reading is not enough: walk the page WITH A MOUSE. For every visible
affordance (button, link, chip, glyph, row, composer):

8. **Every door leads somewhere real.** Clicking must produce the thing
   the affordance names — a transcript door opens the actual conversation,
   never an empty panel; a composer that accepts input must persist it
   (an edit that vanishes on reload is a lie); a chip opens the thing's
   card. An affordance that cannot deliver in live mode must not render.
9. **The next action is visible.** From any organ, the reader can see how
   to ACT on what they read: open the line's conversation, continue it in
   the workspace, add/resolve a plan item, triage a proposal. If acting
   requires knowledge of another surface, fail.
10. **Walk it at every stage.** Repeat the click-walk on a fresh project
    (turn 1), a cohering one, and a mature one — affordances that only
    make sense at one scale fail the arc.

## Discipline

- Evaluate from a screenshot or the live page — never from the World JSON.
- Write the finding BEFORE the fix: what a reader would say, quoted.
- A fix that improves one stage is re-read at every stage (the arc rule).
- The fixture face (`notebook.html` without `?live`) is the design-complete
  reference: when a live page scores below it, name which organ is missing
  (honest projection) versus which is *polluted* (a face bug — fix now).

## Standing findings log

Findings that drove face changes live in `docs/arch/record-face.md`; this
file stays the instrument, not the journal.
