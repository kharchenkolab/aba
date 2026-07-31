# The Record face — a document-first projection of a project

An alternative Contact-plane face: the project rendered as a living lab
notebook (design: `RECORD_DESIGN.md`, repo root). Phase 1 is **read-only** —
a projection of the entity graph, no new writes, no new entities — so any
existing project can be viewed as a Record today, and the classic workspace
and the Record stay two renderings of one substrate (§13.3 of the design).

> Status: current as of 2026-08. Phase 1 (read-only face) only; phases 2–4
> (shared triage, authoring strata, the spine) are design, not code.

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
- **Sittings are derived, provisionally.** Threads conflate a question with
  its one conversation; the Record's episode grain (sittings) is clustered
  from run rows by attention gap (`SITTING_GAP_MINUTES`). Boundaries stay
  heuristic until a sitting owns a distillation record — then it becomes an
  entity and freezes (phase 3; §13.2.5 of the design).

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

- **Maturity** = the claim type's status ladder, ordered at registration;
  the assembler ships the rung, the renderer picks the glyph.
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
| `frontend/src/notebook/main.tsx` | `?live=1[&api=…][&project=…]` opt-in; fixture face is the default |
| `tests/test_record_world.py` | the guard suite (gated): projection, sittings, tray, leftovers |
| `frontend/src/notebook/live.test.tsx` | adapter mapping + renderer smoke over an adapted world |

## Known gaps

- **Prose is titles-only.** `narrative` bodies live in artifact bytes; the
  paragraphs rendered live carry the entity title and attribution only.
  Ratified-prose renditions arrive with phase 3 (revision machinery for
  narrative is also still figure/table-only — see `provenance.md`).
- **The project header is the pid.** No project display-title in World v1.
- **Trails, provenance drawer, briefing, RFC, gestures** render only on the
  fixture face; the API does not carry them yet.
- **Leftovers ignore proposal mentions** (§13.1 wants "no proposal/mention";
  v1 checks edges + pin only).
- **`whats_new` has no per-user cursor** — it is the raw event tail, and
  `log_event` is best-effort; audit per-kind coverage before leaning on it
  (entity-model known gap).
