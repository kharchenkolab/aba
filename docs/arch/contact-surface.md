# Contact surface — the human-facing plane

The frontend: how the scientist *points at* entities and *operates* on them, and why
adding a new entity type is a registration, never a shell edit.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

The Contact plane is a **producer/consumer of the waist** (the entity model), never a
second home for domain logic. One scientist should be able to *point* at any entity —
dataset, run, result, claim — and act on it the same way, and a developer should be able
to add a new bio type without touching the platform shell. Four invariants make that hold:

- **The UI reads and writes ONLY through the model API.** No business logic in the
  client — every mutation is a `fetch` to `/api/…`; the browser holds *view* state (what's
  focused, which column is open), never *domain* state. A pin is `POST /api/entities/{id}/pin`,
  a claim is `POST /api/claims`, a revision is an agent tool call carried by the chat. This
  keeps the waist authoritative and the UI a projection — memory-wipe recovery and
  multi-surface consistency fall out for free.
- **The frontend mirrors the backend entity model — a shell/domain split, lint-enforced.**
  `platform/` is domain-blind; `bio/` holds the domain; `platform/` may never import `bio/`
  nor name a bio type by literal (`platform/__platform_imports.test.ts`). This is the
  frontend mirror of the backend content-pack seam (`scripts/check_seam.sh`,
  `tests/check_platform_purity.py`: `core ↛ content`). The failure it prevents: bio knowledge
  leaking into the shell so every new type forces a shell rewrite.
- **A viewer registry keyed by entity type — open for extension.** A type's view *registers*
  itself (`register_focus_view('claim', …)`); the shell dispatches by lookup
  (`focus_view_for(type)`). Adding a type is a YAML + a `register_*` call, never editing a
  `switch(type)`. An unregistered type renders a generic placeholder, not a white screen.
- **Uniform interaction primitives, specialized only at the edge.** *Focus, highlight,
  reference, revise* are one mechanism each at the core (URL-canonical focus; a composer
  attachment; an id threaded into the prompt; an agent tool). Each *viewer* specializes only
  how it **captures** the gesture — a figure's click-to-region overlay vs a Result's
  per-member highlight — never what the gesture *means*.

## The model

The `frontend/src/` tree is four layers around a composition root (`App.tsx`):

```
platform/   the domain-blind shell — Rail, ChatPane, Composer, Drawer, HResizer, UploadDrop
lib/        typed seams + registry hubs — api.ts, flags.ts, {railIcons,menuActions,typeLabels,
            entityClasses,sectionCounts,projectSignals,homeTiles,searchFacets,messageRenderer}
bio/        the domain — entity-type views (focusViews.tsx), ProjectTree, Message, ResultView,
            ClaimView, RunView, …; bio/index.ts is the aggregator that fires all registrations
components/  shared shell widgets — FocusCanvas, SplitButton, Proposals, SearchModal, …
viewers/    the file/artifact viewer subsystem — registry.ts (by component name) + dispatch.ts
```

**The registry seam is `register_* / lookup_*` pairs.** `bio/` calls the registrars at import
time; the shell calls the lookups at render time and sees only what's registered:

```
bio/index.ts  ── import ─►  lib/railIcons     register_rail_icon('claim', …)   ─► rail_icon_for(t)
(App.tsx does              lib/typeLabels     register_type_label(…)           ─► type_label_or_fallback(t)
 `import './bio'`          lib/entityClasses  register_entity_class(…)         ─► type_in_class(t, cls)
 once, first)             bio/focusViews     register_focus_view('result', …) ─► focus_view_for(t)
```

`import './bio'` (`App.tsx:29`) must run before any shell component asks its registry, or the
shell sees empty defaults. This mirrors the backend exactly: `register_card_builder`
(`core/manifest/assembler.py:24`), `register_viewers_yaml` (`core/viewers/registry.py:45`),
and the declarative entity-type registry (`core/entity_types/registry.py`).

## The four layers & the lint-enforced seam

`platform/__platform_imports.test.ts` is the load-bearing guard. It asserts three things over
`platform/` + `components/`: (1) no file imports from `bio/` (`:81`); (2) no *new* bio type
string literals — `entity.type === 'figure'` (`:103`); (3) no *new* inline `/api/` fetch —
route through `lib/api.ts` (`:124`). Existing violators are **grandfathered in explicit
baselines** (`LITERAL_BASELINE`, `FETCH_BASELINE`) that ratchet down; new code cannot add to
them. The seam that *is* clean today is `platform ↛ bio`: the shell reaches the domain only
through the `lib/` registries, so `platform/Rail.tsx` renders a claim's icon without ever
knowing what a claim is.

`lib/api.ts` is the typed API client seam — `apiGet/apiPost/apiPatch/apiDelete` + typed
helpers (`getEntityProvenance`) that throw `ApiError` on non-2xx. It rides the **global
`fetch` monkeypatch** in `oodBase.ts:19`, which rewrites absolute `/api/` + `/artifacts/`
paths with the deployment's base prefix (the OOD reverse-proxy shim; a no-op in a normal
install). Per-project routing is threaded **per request** as a `?project_id=` query param, not
by that patch (see Known gaps — `api.ts`'s header comment overstates this).

## Registries, not switches — the entity-oriented UI

There are **two** keyed viewer registries, both open for extension:

- **Entity focus-views** (`bio/focusViews.tsx`) — keyed by entity type. `register_focus_view`
  (`:82`) populates a `Map`; `focus_view_for(type)` (`:89`) is the lookup;
  `FocusCanvas.renderRegistryView` (`components/FocusCanvas.tsx:440`) renders whatever comes
  back and falls to a placeholder otherwise. The nine bio views register at the file's
  bottom (`:556–564`), mirroring `content/bio/entity_types/*.yaml`.
- **File/artifact viewers** (`viewers/registry.ts`) — keyed by frontend component name.
  `dispatch.ts:36` (`dispatchViewers`) is a **pure client mirror** of the backend's
  `viewers_for` (`core/viewers/registry.py:88`): the registry is fetched once
  (`/api/viewers/registry`) and cached, so a file click picks its viewer with no network
  round-trip. Backend `Viewer` records (YAML-declared, priority-ranked) are the source of
  truth; the client just re-runs the same ext/MIME/entity-type match.

The declarative type catalog is fetched by `entityTypes.ts` from `/api/entity-types`
(`main.py:489`) once at mount (`App.tsx:254`) and cached; `typeOf` / `typeHasChatGesture` /
`typeInCategory` let shell components dispatch on capabilities (which gestures a type offers)
instead of hardcoded sets. This is the frontend mirror of `core/entity_types/registry.py`.

**Projection: entity → focus-view.** Given the focused entity, `FocusCanvas` looks up its
view and renders it inside a uniform frame (header, provenance panel, meta). The *other*
projection — entity + focus → the agent's **per-turn manifest cards** — is built backend-side
by per-type card builders (`content/bio/cards/*.py` → `core/manifest/assembler.py:160`),
shipped as the first SSE event of a turn (`useChat.ts:745`), and surfaced in the ⓘ Drawer.
That manifest→card pipeline is **owned by [`context-and-memory.md`](context-and-memory.md)** —
this doc owns only its display. The [`provenance.md`](provenance.md) sibling owns the content
of the Provenance panel (`FocusCanvas.tsx:549`); the entity graph it reads is owned by
[`entity-model.md`](entity-model.md).

## Deixis & the uniform primitives

**The URL is the deixis state.** `useUrlState` (`App.tsx:119`) makes project / thread /
focused-entity / section / scene canonical in the route (`/p/<pid>/data/e/<did>`), so a
reload, deep link, or Back lands on the same pointing. `focusedId === 'workspace'` is the
"nothing focused" sentinel. Focus then flows to the agent: `useChat` sends `focus_entity_id`
(+ an optional `focus_member_id` for a multi-panel Result's active viewport) and `thread_id`
in the `/api/chat` body (`useChat.ts:516`) — that is how the human's "I'm looking at *this*"
reaches the Reasoning plane (turn mechanics owned by [`agent-loop.md`](agent-loop.md); the
SSE reader is the pure `lib/sseReader.ts`).

The four primitives are one type-agnostic mechanism each, specialized only at the viewer edge:

| Primitive | Core mechanism | Edge specialization |
|---|---|---|
| **focus** | URL `setFocus` → `focusedId` → `focus_entity_id` in `/api/chat` | which panel a Result reports as its active member |
| **highlight** | freehand region capture → image+note attached to the composer (`attachAnnotation`) | figure overlay (`AnnotatedFigure`) vs Result per-member surface vs chat-message highlight |
| **reference** | an entity id threaded into the prompt (`chatAboutResult`'s `entity_id="…"` clause, `App.tsx:588`); pin/keep/claim `POST` the citation | figure→claim, result→finding, keep-a-message |
| **revise** | a tailored prefill that steers the agent to `make_revision` / `reproduce_from_exec` (`SplitButton`, `FocusCanvas.tsx:335`) | figures/tables/results expose the split-button; others don't |

Every one of these ends in a `fetch` to the model API or a message to the agent — none mutate
domain state in the client.

## Key implementation references

| Where | What |
|---|---|
| `frontend/src/App.tsx` | composition root; `useUrlState` deixis; focus/thread/scene routing; the interaction-primitive call sites (`goToEntity`, `chatAboutResult`, `pinEntity`) |
| `frontend/src/platform/__platform_imports.test.ts` | the seam lint: `platform ↛ bio` (`:81`), no new type literals (`:103`), no new inline fetch (`:124`) + the burn-down baselines |
| `frontend/src/bio/index.ts` | the aggregator — `import './bio'` fires every `register_*` into the `lib/` hubs |
| `frontend/src/bio/focusViews.tsx` | the entity-type **viewer registry** (`register_focus_view` / `focus_view_for`) + the 9 bio views |
| `frontend/src/components/FocusCanvas.tsx` | the shell frame that dispatches via `focus_view_for` (`:440`); residual type literals (the 3.4c burn-down) |
| `frontend/src/viewers/{registry.ts,dispatch.ts,types.ts}` | file/artifact viewers; `dispatch.ts` pure-mirrors `core/viewers/registry.py:viewers_for` |
| `frontend/src/entityTypes.ts` | runtime type catalog from `/api/entity-types`; capability lookups (`typeOf`, `typeHasChatGesture`) |
| `frontend/src/lib/{api.ts,flags.ts}` | typed API client seam + `ApiError`; frontend feature flags |
| `frontend/src/oodBase.ts` | the global `fetch`/`EventSource`/img-`src` monkeypatch — base-prefix routing |
| `frontend/src/useChat.ts` · `lib/sseReader.ts` | chat turn state; sends `focus_entity_id`; consumes the SSE manifest (first event) |
| `core/viewers/registry.py` · `main.py:2403` | backend viewer registry + `/api/viewers/registry` wire |
| `core/entity_types/registry.py` · `main.py:489` | declarative type registry + `/api/entity-types` catalog |
| `core/manifest/assembler.py` · `content/bio/cards/*.py` | per-type card builders (manifest projection — see `context-and-memory.md`) |

## Known gaps

- **`bio ↔ components` is a bidirectional import cluster.** `components/` imports `bio/` in 4
  files (`FocusCanvas` → `focus_view_for`/`AnnotatedFigure`, `ResultList`/`PreviewWindow` →
  `HighlightableImage`) while `bio/` imports `components/` in 7 (icons, `SplitButton`,
  `ConfirmDialog`, `ResultList`, `highlightTools`). Import-safe, but a cohesion tangle: the
  clean seam is `platform ↛ bio`, and `components/` sits ambiguously between shell and domain.

- **The typed API seam is bypassed wholesale.** ~132 inline `/api/` fetches (I count 138 raw
  `fetch(` in non-test `frontend/src/`, 107 hitting `/api/`) route around `lib/api.ts`; the
  ratchet only stops *new* ones in `platform/`+`components/` — `bio/` isn't ratcheted at all.
  Real routing rides the `oodBase.ts` patch (base prefix) plus per-request `?project_id=`; the
  `api.ts` comment claiming the app "patches to carry project_id" is inaccurate — no such
  global patch exists, project_id is threaded call-by-call.

- **Viewer selection is half-registry, half-switch.** File viewers and focus-views both
  dispatch through registries, but `FocusCanvas` still carries residual `entity.type` literals
  (`workspace`/`thread`/`figure`/`analysis`/`result`/`table` at `:57,108,134,161,345`) for
  header chrome, compare mode, annotate routing, and action buttons — grandfathered in
  `LITERAL_BASELINE`, the unfinished "3.4c" burn-down. The shell is not yet fully type-blind.

- **Two parallel entity-type projections.** Import-time `bio/` registries in `lib/` (labels,
  classes, icons, section counts, focus-views) coexist with the runtime `/api/entity-types`
  catalog (`entityTypes.ts`); metadata like a type's display label lives in both. The catalog
  is fetched but the shell still leans on the import-time registries for most dispatch — a
  consolidation the type-registry work started but didn't finish.

- **God-components.** `bio/Message.tsx` (1235 lines), `App.tsx` (1086), `useChat.ts` (1033),
  `bio/ResultView.tsx` (981), `platform/Drawer.tsx` (971) each carry several axes; the audit's
  standing recommendation is to extract routing/data hooks and keep leaf components leaf-only.
