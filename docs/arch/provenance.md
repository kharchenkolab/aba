# Provenance & reproducibility

How every entity carries a typed, enforced record of *how* it was made and *who* made
it — and how that recorded work is reproduced, revised, and recovered.

> Status: current as of 2026-07. This is the **maintained** reference.

## Aims & principles

Verifiability is a **cross-cut**: it must attach to *every* entity uniformly, from every
producer, without the producer opting in. Convention doesn't scale to that — one forgotten
field and an entity's origin is lost forever. So the model is **provenance by
construction**:

- **You cannot create an un-provenanced or un-attributed entity.** Not "should" —
  *enforced*. Every entity carries a typed `derivation` (how) and an `actor` (who); a
  build-time ratchet fails CI on any create site that omits it, a legacy backfill on
  project open closes the historical tail, and a coverage invariant asserts the result.
- **Capture is automatic and semantics-agnostic.** The engine records *how* and *who* the
  same way for a scRNA figure, a narrative note, or an imported dataset — it never reasons
  about the science. A run writes its exec record as a *side effect*; the actor comes from
  ambient context, not a parameter the caller must remember to pass.
- **Separate cheap attribution from expensive reproducibility — deliberately.** The
  `derivation`+`actor` fields are tiny and always-present (**~100% coverage**). The exec
  record (the code+env needed to *re-run*) is heavier and **best-effort (~95%)**.
  Conflating them would drag field coverage down to reproduction coverage; keeping them
  apart means "who made this?" always answers even when "re-run it" can't.
- **Reproduction keys off the exec record, never off provenance.** `exec_id → code + env`
  is the reproduction handle. `derivation`/`actor` are *descriptive* — they answer
  questions, they don't drive re-execution. A wrong actor never corrupts a reproduce; a
  missing exec record never hides an origin.

## The model

Three things ride on every entity row, plus a sidecar off to the side:

- **Exec record** — the reproduction unit. A thin index row in `execution_records`
  (`exec_id`, `thread_id`, `run_id`, `tool_use_id`, `tool_name`, `status`, `code_hash`,
  `record_path`, timing) **plus a JSON sidecar** at `<cwd>/.exec/<exec_id>.json` colocated
  with the run's workdir. The sidecar holds `code`, `executor`, `language`,
  `package_versions`, `env_fingerprint`, `produced[]`, and stdout/stderr tails. An entity
  points at its producing run by `exec_id`; producing code no longer lives on the entity
  row (`exec_records.py:46`, `_schema.py:374`, `run_exec.py:446`).
- **`derivation`** (JSON, `entities.derivation`) — *how* it came to be: one of
  `exec(exec_id)`, `derived_from([ids])`, `imported(source)`, `manual()`, `legacy()`
  (`derivation.py:26`).
- **`actor`** (string, `entities.actor`) — *who* made it: `agent:<run_id>`,
  `human:<uid>`, `system`, or `legacy` (`derivation.py:56`).

Descriptive provenance (`derivation`+`actor`) and reproducible provenance (the exec
sidecar) are two systems that co-locate on the row. The `exec` derivation kind is the only
bridge — it *names* an exec record; the other four kinds have no sidecar.

```
entity ──derivation──▶ {kind, …}          "how"  (always present, enforced)
   │   ──actor───────▶ agent:… | human:…   "who"  (always present, enforced)
   └── exec_id ───────▶ execution_records row ──▶ .exec/<id>.json
                         (thin index)             code · env · produced
                                                  "re-run me"  (best-effort)
```

Both columns are nullable at the SQL level (added by `ALTER TABLE`, `_schema.py:208`); the
"no NULL derivation" property is a **checked invariant**, not a column constraint — held by
the three mechanisms below.

## Capture & enforcement — you can't escape it

Three cooperating mechanisms make *no entity has `derivation IS NULL`* true, none of them
convention:

1. **Auto-derivation at the create seam.** `create_entity` turns a supplied `exec_id` into
   `derivation=exec(exec_id)` automatically, and defaults `actor` from the ambient
   contextvar (`current_actor()`), so every exec-born path (figures, tables, cells,
   revisions, materialize) is provenanced without passing anything explicit
   (`entities.py:104`).
2. **The build-time ratchet.** `tests/check_derivation.py` AST-walks all of `backend/` and
   fails if any `create_entity` call lacks `derivation=` or `exec_id=`; it runs as a CI
   invariant (`test_invariants.py:24`). Enforcement is at the *source*, not a runtime raise
   — `create_entity` will still insert a NULL if mis-called, so the ratchet's job is to
   guarantee no source mis-calls it, and the backfill below catches anything that predates
   the rule.
3. **Legacy backfill + coverage invariant.** On every project open `backfill_derivations()`
   runs (idempotent, guarded, touches `derivation IS NULL` rows only): `exec_id → exec`,
   else a "came-from" edge → `derived_from`, else honestly `legacy()` — **never a
   fabricated origin**; the historical actor is unknowable → `legacy`
   (`derivation_backfill.py:23`, `projects.py:249`). `derivation_coverage_violations()`
   asserts full coverage afterward (`test_phase2_backfill.py:95`). A parallel one-shot,
   `backfill_legacy_producing_code`, reconstructed the dropped `producing_code` column into
   synthetic exec records so post-cutover read paths still resolve (`exec_records.py:392`,
   run on `init_db`).

Actor wiring is **boundary-set**: HTTP mutating routes bind `human:local`, the agent turn
loop binds `agent:<run_id>` via `acting_as(...)`, and exec-born materialize passes
`agent_actor_for_exec(exec_id)` explicitly — it holds the run_id the contextvar can't reach
across the gateway thread (`actor.py`, `derivation.py:84`).

Exec-record capture itself is a **side effect** of `run_python`/`run_r`:
`_write_exec_record` fires on every kernel-path dispatch, resolving the active Run,
snapshotting package versions + env fingerprint (session-cached), and unioning
plots/tables/files into `produced[]` (`run_exec.py:446`). It is **best-effort** — any
failure is logged and swallowed so provenance never blocks the user's result. That swallow
is precisely the ~100%-field / ~95%-reproducibility split: the field is enforced, the
sidecar is not. The env manifest is deduped content-addressed by `env_fingerprint` (one
blob per unique env, re-inflated transparently on read — `exec_records.py:115`).

## Reproduce, revise, export — all keyed off `exec_id`

- **`reproduce_from_exec(entity_id)`** — the "re-run / verify this still works" path.
  Fetches the entity's exec record, re-runs its code in the current kernel, and reports
  `env_drift` by comparing the original vs new `env_fingerprint`. Creates no entity. This
  *is* the reproduction engine; it reads `exec_id`, never `derivation`/`actor`
  (`revisions.py:712`).
- **Revisions** over the `wasRevisionOf` chain (figure/table): `make_revision` runs
  modified code and pins the new artifact as a sibling of the parent (linear-chain guard;
  superseding is opt-in via `supersede_newer`), `set_current_revision` flips which chain
  entry is displayed (fully reversible), and `delete_revision` hard-deletes one entry while
  re-parenting its children and re-anchoring any promoted Result onto the new head.
  Language is sniffed from the code *about to run*, not the parent's record — running the R
  interpreter on Python code was the 2026-06-11 live bug (`revisions.py:183`, `:547`,
  `:424`).
- **Diagnostics & export** (rare escape hatches): `diff_env` reports package deltas between
  the run's env and now; `rebuild_env` reconstructs a throwaway isolated env pinned to the
  recorded versions (bisect a drift via `only=`); `export_bundle` writes a portable
  directory — script + pinned `requirements.txt` + inputs-by-identity/hash + exec record +
  README, with inputs **referenced, not copied** (genomic files are large). These surface
  to the agent as the MCP tools `reproduce_from_exec` / `diff_env` / `rebuild_env` /
  `export_reproduction_bundle` (`revisions.py:786`, `:814`, `:837`; MCP registration in
  `.../aba_core/tools/revisions.py`).
- **The promotion act, surfaced.** `promotion_record(entity)` reads a `derived_from`
  entity's `actor` + `created_at` + `sources` into "promoted by *who* from *what* on
  *when*", attached to the canvas Provenance panel alongside the upstream/downstream
  neighborhood (`provenance.py:114`, `results.py:202`).

**Memory-wipe recovery** — rebuilding a wiped agent context from these durable records — is
owned by [`context-and-memory.md`](context-and-memory.md); the exec records + derivation
graph are its substrate. The **entity graph** these fields hang on (rows, edges, the store,
promotion) is owned by [`entity-model.md`](entity-model.md); this doc owns only what makes
it *verifiable*. The agent turn that binds `agent:<run_id>` is
[`agent-loop.md`](agent-loop.md).

## Key implementation references

| Where | What |
|---|---|
| `core/graph/exec_records.py` | exec-record CRUD: `create` (row+sidecar, `:46`), `get` (merge + env re-inflate, `:185`), `list_by_run`/`list_by_thread`, `aggregated_code_for_run`, env-manifest dedup, `backfill_legacy_producing_code` (`:392`) |
| `content/bio/tools/run_exec.py` | `_write_exec_record` (`:446`) — automatic capture on every kernel-path `run_python`/`run_r` (code, code_hash, env fingerprint, `produced[]`, timing) |
| `core/graph/derivation.py` | `derivation` kinds + `actor` constructors; `VALID_KINDS`, `from_lineage`, `agent_actor_for_exec` |
| `core/graph/entities.py` | `create_entity` — auto-derive `exec`→derivation (`:104`), default actor from context |
| `core/runtime/actor.py` | ambient actor contextvar: `current_actor`, `acting_as`, boundary binding |
| `tests/check_derivation.py` · `tests/test_invariants.py` | the build-time ratchet (every `create_entity` has `derivation=`/`exec_id=`) + its CI invariant (`test_derivation_invariant`, `:24`) |
| `core/graph/derivation_backfill.py` | project-open backfill + `derivation_coverage_violations` (the runtime coverage invariant) |
| `content/bio/lifecycle/revisions.py` | `make_revision`, `set_current_revision`, `delete_revision`, `reproduce_from_exec`, `diff_env`, `rebuild_env`, `export_bundle` |
| `core/graph/provenance.py` | upstream/downstream walks + `promotion_record` (who/when/from) |
| `content/bio/{mcp_servers/aba_core/tools,web/routes}/revisions.py` | agent (MCP) + HTTP surfaces for revise / reproduce / export |

## Known gaps

- **Whole-Run replay (`reproduce_run`) is designed but not built.** `reproduce_from_exec`
  re-runs *one* artifact's exec; nothing replays an entire Run's exec chain step-by-step
  against the current env. The pieces exist (`list_by_run` + `aggregated_code_for_run`);
  the driver does not — **confirmed absent from `backend/`**.
- **`inputs` and `seed` are not captured yet.** `_write_exec_record` records code + env +
  `produced` + timing, but **not** the run's inputs or RNG seed. So `export_bundle`'s `inputs.json` is thin, drift-vs-inputs
  isn't checkable, and a re-run isn't bit-stable — the reproduction envelope is *code+env*
  today, not the full `code/env/inputs/seed`. This is the substantive reason reproducibility
  trails field coverage.
- **`execution_records` has no `actor` column.** Run-level attribution rides on `run_id`
  (→ `agent:<run_id>`) and the produced entity's own `actor`; a first-class actor on the
  exec row is deferred.
- **Real `human:<uid>` is deferred.** Single-user deployments attribute every human action
  to `human:local` (the `human_actor` default); real identities wait on the access cross-cut
  ([`deployment-and-access.md`](deployment-and-access.md)).
- **Revisions are figure/table-only.** `make_revision` / `reproduce_from_exec` /
  `delete_revision` raise on any other entity type; a general "revise any exec-born entity"
  path isn't built.
- **Capture is code-run-scoped and best-effort.** `run_python`/`run_r` write exec records on
  the success path — both interactively and as background jobs (`_write_exec_record_for_job`
  stamps the same envelope, [`jobs-and-hpc.md`](jobs-and-hpc.md)); a capture failure is logged
  and silently swallowed (the ~95% reproducibility side of the split). Producers that never run
  code — a manual create, a promotion — carry `derivation`/`actor` but no sidecar.
- **Backfilled records are degraded.** Synthetic exec records carry `source:"backfill"` with
  no env fingerprint or `produced[]`, so `diff_env` / drift is meaningless for pre-cutover
  entities (UI can flag them off that marker).
