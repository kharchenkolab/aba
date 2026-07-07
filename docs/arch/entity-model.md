# The entity model — the waist

The typed, persistent graph both the scientist and the agent read and write: the
**contract** at the center of ABA, not the database that happens to hold it.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

The entity model is the **narrow waist**: every plane (Compute, Reasoning, Contact) and
every cross-cut talks to the science *only* through this typed graph. A narrow waist is
worth nothing if it isn't narrow — so the one imperative is **protect the contract, not
the store**. Everything below is that imperative applied:

- **The contract is the typed graph — handles, edges, provenance refs, focus — never the
  bytes.** An entity row carries a small typed record and a *path* to its artifact
  (`artifact_path`); bulk content stays on disk, referenced, never inlined
  (`store.register` is store-by-reference — `core/data/store.py:5`). Keeps the waist thin
  enough that both a human UI and an LLM context can hold the whole shape of a project.
- **The store is a swappable adapter behind a read-port.** Callers query by *predicate*
  (`find_entities`/`exists_entity`), never by reaching for SQL. The raw connection
  (`_conn`) is confined to `core/graph/` and CI-ratcheted (`tests/check_store_port.py`),
  so the backing store can change without touching a single consumer. Prevents the failure
  where "swap the DB" means editing 40 files.
- **New entity types by registration, not core surgery.** A type is a YAML file, not a
  schema column or an `if type == …` branch. The `entities` table is type-agnostic (`type`
  is an opaque string); the domain vocabulary lives in the content pack. Prevents the
  schema growing one column per bio concept — the content-pack seam (`core ↛ content`).
- **Every write goes through the *bound* project's DB.** Which SQLite file a write lands in
  is resolved per execution context, not from a mutable global. Prevents the cross-project
  corruption a shared global caused (see [Per-project binding](#per-project-binding-every-write-hits-the-bound-db)).
- **Promotion is bilateral and explicit.** Substrate becomes an entity only when a human
  *pins* it or the agent *asserts* "this is a result" — never silently. Keeps the graph the
  curated set of things worth reasoning about, not a dump of every intermediate.

## The model

Two tables carry the graph; a registry types it; a contextvar scopes it.

```
        entity_types/*.yaml  ──registers──►  registry (open type set + capability flags)
                                                   │ validates
   entities ──────────── entity_edges ────────────┘
   (typed handles)       (typed relations, PROV-O)
     id, type,             source_id --rel--> target_id
     title, status,        UNIQUE(src,tgt,rel)  (idempotent)
     artifact_path ──► bytes on disk (not in the waist)
     metadata (JSON), derivation, actor ──► provenance refs
```

- **Entity** — one row in `entities` (`core/graph/_schema.py:166`): an `id`, an opaque
  `type` string, `title`, `status`, an optional `artifact_path` (the handle to bytes),
  small `metadata` JSON, and provenance columns `derivation`+`actor` (who/how it came to
  be). CRUD is `core/graph/entities.py` — `create_entity`, `get_entity`, `update_entity`,
  `archive_entity` (soft-delete), `delete_entity_hard`. Types are opaque here: the module
  header states it is domain-neutral, and bio names appear only in the workspace bootstrap
  row (`_schema.py:414`).
- **Edge** — one row in `entity_edges` (`_schema.py:221`): `source_id --rel_type--> target_id`,
  `UNIQUE(source,target,rel)` so `add_edge` is idempotent (`INSERT OR IGNORE`,
  `core/graph/edges.py:42`). Relations are W3C PROV-O + ABA extensions (`wasDerivedFrom`,
  `wasGeneratedBy`, `supports`, `includes`, `wasRevisionOf`, …). Reads are `edges_from` /
  `edges_to`.
- **Type registry** — `core/entity_types/registry.py`: an `EntityTypeSpec` per YAML
  (`registry.py:34`), loaded at startup by the content pack (`content/bio/__init__.py:11`
  → `load_types`). It is the *open set* — re-loading replaces; a new type is a new file.
- **Focus** — the pointer at what the scientist is looking at. It lives in the graph as a
  plain `focus_entity_id` reference on messages/runs (`core/graph/messages.py:19`,
  `_schema.py:149`); its per-turn projection into the agent's context (the Manifest) is
  owned by [`context-and-memory.md`](context-and-memory.md).

Provenance is a first-class part of the waist — the typed `derivation`+`actor` on every
row and the `exec_id` pointer into the exec record — but its mechanics (exec records,
revisions, reproduce/revert) are owned by [`provenance.md`](provenance.md).

## The store and its read-port

Callers read the graph by **predicate**, not by SQL. `find_entities(**predicates)`
(`entities.py:258`) is the typed read surface — filter by `type`/`type_in`, `status`,
`parent_entity_id`, `metadata_contains`, `text_query`, ordering, paging — and
`exists_entity(**predicates)` (`entities.py:330`) is a limit-1 existence check. `get_entity`
and `count_entities` round it out. No consumer opens a connection.

The enforcement is a **ratchet**: `tests/check_store_port.py` scans the backend and fails
CI if `_conn()` (the raw `sqlite3` handle, `_schema.py:88`) is called outside
`core/graph/`. A short `ALLOWLIST` grandfathers the sites not yet migrated; a *new* raw-SQL
caller cannot be added. This is what makes the store an adapter rather than the contract:
the read-port is the seam, `_conn` is behind it.

Writes are validated against the registry at the boundary, **hard-reject** (not
warning-only): `create_entity` calls `check_create_fields` and raises `ValueError` on a
missing required field (`entities.py:78`); `add_edge` calls `check_edge` and rejects an
edge whose `(src, tgt, rel)` triple isn't declared in the endpoints' `allowed_edges`
(`edges.py:12`). Unknown types pass through untouched (legacy data, synthetic test types).
The bio router converts the `ValueError` to HTTP 422. (NB: the registry module docstring
still reads "ENFORCEMENT IS NOT WIRED YET" — `registry.py:16`; that is **stale**. The
validators *are* wired at the write boundaries; trust the code.)

## The type registry — extension without surgery

An `EntityTypeSpec` declares a type's `display`/`icon`, `schema` (required/optional
fields), `status_model` (states + transitions + `initial`), `allowed_edges` (`out`/`in`
rel lists), `focus`/`ui` pointers, and a **capabilities** dict of coarse flags
(`registry.py:61`). The flags are how *core/platform* queries behavior without naming bio
types by literal:

- `is_artifact` / `is_run` → what counts as a harvestable output vs. a run container
  (`figure`, `table`, `cell` are artifacts; `analysis` is a run) — consumed by e.g.
  `core/jobs/continuation.py:116`.
- `artifact_group` (`plots→figure`, `tables→table`) → `registry.artifact_groups()`, used by
  the job runner's harvest (`core/jobs/runner.py:528`).
- `by_title_storage` (`artifact_file` / `run_dir` / `data_path`) → the on-disk layout the
  recovery archive rebuilds by title (`core/recovery/by_title.py:239`).
- `sidebar` → the Contact-plane sidebar rendering; viewer selection keyed by type is owned
  by [`contact-surface.md`](contact-surface.md), and the bundle/type-registry flag surface
  by [`bundle-and-content.md`](bundle-and-content.md).

`HIDDEN_TYPES = ("capability", "reference")` (`entities.py:17`) keeps infrastructure kinds
off the project tree while leaving them real, queryable entities. To add a type: drop a
YAML in `content/<pack>/entity_types/`. No core edit, no column, no migration.

## Per-project binding — every write hits the bound DB

Each project is its own SQLite file (`projects/<pid>/project.db`); only the DB path is
per-project, artifacts stay global (`core/projects.py:1`). Which file `_conn()` opens is
resolved live, and the resolution order is deliberate:

1. a **context-bound** override (`_active_db_path`, a `contextvars.ContextVar`,
   `_schema.py:66`) if set for this asyncio task/thread — this **wins**;
2. else the process-global `DB_PATH`.

`projects.bind(pid)` (`core/projects.py:254`) sets *both* the context-bound DB path and the
context-bound active project (`_active_pid`, `projects.py:44`) for the current context only,
and `projects.current()` prefers the contextvar. Background turn tasks wrap their whole run
in `with bind(pid):`, which `asyncio.create_task` copies into child contexts — so a
concurrent request that repoints the process-global `DB_PATH` **cannot** swap the database
out from under a running turn. This is the fix for the **2026-06 cross-project corruption
incident**: a turn read another project's messages mid-loop because a poll for a different
project called the global `set_db_path` (`_schema.py:45`, whose docstring now carries the
DANGER note). SINGLE/test mode (`ABA_DB_PATH` set) bypasses the whole layer — the harness
owns one DB.

The hazard that remains: binding is **ambient**. The store verbs carry no project argument;
correctness depends on the caller having entered `bind(pid)` first. A write with **no**
bound project lands in a fallback DB — `create_entity` warns once when
`projects.current()` is `None` (`_warn_if_unbound`, `entities.py:23`), but nothing hard-stops
it. See Known gaps.

## Promotion — substrate becomes an entity

Substrate (scratch files, run intermediates, un-pinned outputs) is *not* in the graph until
something promotes it — and promotion is **bilateral and explicit**:

- **The scientist pins.** A UI Pin gesture converges on `pin_evidence`
  (`content/bio/lifecycle/promote.py:96`): it creates (or extends) a `result` entity
  wrapping a figure/table/cell, edged `supports`/`wasDerivedFrom`/`includes` to its
  evidence. `demote` is the inverse — `unpin_evidence` (`promote.py:239`) and
  `archive_entity` (soft-delete, `entities.py:388`).
- **The agent asserts.** Tools `promote_to_result`, `create_finding`, `create_claim`
  (`content/bio/mcp_servers/aba_core/tools/curation.py`) mint higher-level entities; a
  scratch file becomes a tracked artifact via `store.register(...)` (store-by-reference,
  `core/data/store.py:58`).
- **Who did it is recorded.** `create_entity` defaults `actor` from the ambient context
  (`core/runtime/actor.py` — `human:<uid>` on a mutating HTTP route, `agent:<run_id>` inside
  a turn) and `derivation` from an `exec_id` when present (`entities.py:104`). So every
  promotion carries its attribution for free.

Note a naming collision: `store.promote(entity_id, to_scope)` (`store.py:97`) is a
*different* axis — it elevates an existing entity's **scope** (project → lab/shared), a P0
scope-tag flip with no byte movement yet. It is not substrate→entity promotion.

## Key implementation references

| Where | What |
|---|---|
| `core/graph/_schema.py` | `entities` + `entity_edges` schema; `_conn` (the sole raw handle); `active_db_path` / `bind_active_db` / the `_active_db_path` contextvar; `set_db_path` (+ DANGER note); `init_db` |
| `core/graph/entities.py` | entity CRUD; the read-port `find_entities` / `exists_entity`; write-boundary schema validation; `HIDDEN_TYPES`; `_warn_if_unbound` |
| `core/graph/edges.py` | typed edges; `add_edge` (idempotent) / `remove_edge` / `edges_from` / `edges_to`; `_edge_validate` |
| `core/entity_types/registry.py` | `EntityTypeSpec`, `load_types`, capability queries (`types_with`, `artifact_groups`, `by_title_storage`), `check_create_fields` / `check_edge` |
| `content/bio/entity_types/*.yaml` | the bio type set (dataset/analysis/figure/table/cell/result/finding/claim/…) + their capability flags |
| `core/projects.py` | per-project DB registry; `bind` (contextvar scoping) / `current` / `set_current` / `ensure_opened` |
| `core/data/store.py` · `workspace.py` | membrane verbs (`resolve`/`register`/`promote`/`version`, store-by-reference); the scratch tier (un-promoted substrate) |
| `content/bio/lifecycle/promote.py` | `pin_evidence` / `unpin_evidence` — the bilateral promotion/demote flow |
| `tests/check_store_port.py` | the CI ratchet confining `_conn()` to `core/graph/` |

## Known gaps

- **Store read-port burn-down is incomplete.** The ratchet grandfathers 8 sites
  (`check_store_port.py` `ALLOWLIST`). Live raw-SQL includes `core/runtime/checkpoint.py`
  on the *messages*/*runs* tables (`checkpoint.py:161+`) and **4 `wasRevisionOf` edge-walks**
  (`content/bio/lifecycle/revisions.py:104`, `content/bio/graph/figure_history.py:48/77/104`)
  — there is **no edge read-port** yet for multi-hop lineage walks, only single-hop
  `edges_from`/`edges_to`, so revision/history traversal drops to SQL. Also unmigrated:
  lexical search, tool telemetry, budget summary, `projects.py`, `main.py`.
- **No single promotion gate.** Substrate → entity happens via several paths
  (`pin_evidence`, `store.register`, `create_entity` directly, `promote_to_result`).
  Attribution rides the `actor` contextvar default rather than a unified `promote()` chokepoint,
  so a path that forgets to set the actor context yields `actor=None` (backfilled later, not
  enforced at write).
- **Store scoping is ambient-DB only.** `resolve`/`register`/`find_entities` take no project
  parameter; isolation depends entirely on the caller having entered `projects.bind(pid)`.
  An unbound write is warned-once, not refused — a genuine hard access-gate (require a bound
  project to write) is a separate invariant, not yet enforced here.
- **`store.promote` is a P0 stub.** Cross-scope promotion (project → shared) only flips the
  scope tag; the content-addressed reference tier that would hash+place the bytes (P4) is
  designed but unbuilt (`store.py:97`).
