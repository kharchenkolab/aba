# Run outputs — durability and the consumption path

One coherent story for what happens to the files a Run produces: who keeps the
bytes, who records where they are, and how every way a user touches them
(serve, list, view, render, download) resolves them — **wherever they live**.

> Status: current as of 2026-07. The maintained reference for
> `content/bio/lifecycle/runs.py`'s resolver layer, `core/compute/retention.py`,
> and the consumption routes. Design history: `misc/output_durability.md`,
> `misc/output_serving_model.md`.

## Aims & principles

- **Location transparency.** An output is the same first-class object whether it
  was produced locally or on a remote node; its whereabouts is *information*,
  never a precondition. **Failure this prevents:** five consumption surfaces
  each independently equating "exists" with "exists on the controller's disk" —
  fabricated placeholders, dead links, false "missing" answers while the bytes
  sit durably one recorded hop away.
- **Weft owns the bytes; ABA owns the decisions.** The substrate's retention
  index (`retained(label=run_id)`), terminal inventories, and data-plane are the
  system of record for placement and durability; the Run entity holds handles
  (`metadata.weft_targets`, keep decisions, sites) and the exec record holds
  per-step placement (`compute` block). Nothing in ABA re-derives byte truth.
- **Lose bytes, never knowledge — and never lie.** Listings render from recorded
  truth (states `retained / saving / in-store / at-risk / in-sandbox / cleared /
  unknown`), stay honest through sweeps and index outages, and name the site
  when bytes aren't here.

## The invariant: one locate, one mover

The local-or-remote decision has exactly **one home** —
`locate_run_output(run_id, name, match=, remote=)` — and byte movement has
exactly **one door** — `materialize_run_output(loc, max_bytes=, force=,
progress=)`. Everything else is a thin, named policy over that pair.

**`locate_run_output` never transfers.** It walks the local tiers
(weft retained tree catalog-first → live weft jobdir(s) → run sandbox →
exec-cwd (a detached job's own scratch dir, `dirname²` of an exec `record_path`)
→ weft's own `(run, rel)` key → harvested-artifact tier (the run's advertised
`produced[]` serving copies, `durability="store"`)) and then the remote tier
(the Run's non-local targets, confirmed by live-aware `file_stat` for a file or
inventory membership for a directory store), returning `{local_path?, locality:
local|remote, site, durability, kind, size, digest, target}`. `match="exact"`
joins the exact rel only (serve/archive/keep — a same-named file elsewhere must
not answer); `match="name"` adds store-prefix and basename matching
(viewer/lookup).
**Failure this prevents:** N surfaces × M reimplemented resolvers, each with its
own local-fs assumption; lookups (menus, stats, renders) silently moving bytes.

**`materialize_run_output` is the only byte-mover**, and movement is always
deliberate: the calling *action surface* chooses the budget —
request-blocking serves (`/api/runs/{id}/file`, archive, entity/tree downloads)
pass the small transparent gate (`_MAX_HARVEST_BYTES`); the explicit viewer
launch (`resolve_run_store`, a background prepare job with a progress page)
passes the transfer guardrail (`FETCH_GUARDRAIL_BYTES`) and threads `progress`;
`force=True` is the user's explicit override. An unknown size (including a
truncated inventory) refuses. Transports reuse existing primitives: ≤8 MB via
the `file_read` preview channel; bigger on a live kernel via the datasets
data-plane on the sandbox abs path (`register_source → fetch` — retain defers on
a live kernel); bigger on a finished target via a location-axis
`retention.retain(dest="@workspace")` into the retained tree.
**Failure this prevents:** consent/size policy buried inside a resolver (a 2 GB
"transparent" pull one path, a 60 MB refusal on another), and fetches a user
never asked for.

**Caching is only valid against a freshness digest.** Fetched copies land in the
run's `<run_id>-fetched` scratch cache, installed atomically (unique `.partial`
temp → `os.replace`; at install time a dest that already matches the *current*
digest is kept, never destroyed) and stamped with the source digest captured at
locate time — for a file `(bytes, mtime)`, for a store a hash of the sorted
member `(path, bytes, mtime)` lines (the data-plane fingerprint idiom). A
finished target's digest never changes → cache hits forever; an OPEN run's
changes on any write (even a same-size rewrite) → re-fetch.
**Failure this prevents:** a frozen first fetch of a still-growing output served
as if current; half-written files observable mid-fetch; a concurrent open
deleting a fresh copy out from under a viewer.

## The consumption surfaces (all through the canonical pair)

| Surface | Entry | Policy |
|---|---|---|
| Serve (run file) | `web/routes/runs.py` `/api/runs/{id}/file` | `resolve_run_file` (exact, small gate) → preview read → honest site-naming 413 |
| Serve (archive) | `/api/runs/{id}/archive` | per-file `resolve_run_file`; skipped files listed in-zip, never dropped |
| Serve (entity / tree) | `main.py` `/api/entities/{id}/download`, `web/routes/files.py` content/raw/download | dangling `/artifacts` cache → `resolve_entity_output` → materialize under the small gate, else site-naming 413 |
| List | `run_durable_view` / `run_durable_tree` | recorded truth first; two-axis badges (protection × location); `retained` rows always link the live `/file` URL — remote in-place included |
| View | `viewers` routes + external launcher `_resolve_source` | lookup (`resolve_project_run_output`) returns a **remote marker**, moves nothing; launch calls `resolve_run_store` (guardrail budget, progress, retain-on-view) |
| Render | cards / `metadata.run.sites` / exec `compute` block | reads recorded placement only; never a live stat |

## Key implementation references

- `content/bio/lifecycle/runs.py` — `locate_run_output`, `materialize_run_output`
  (+ `_materialize_file` / `_materialize_store`, `_store_members`, stamps), the
  policy shims (`resolve_run_file`, `resolve_output`, `resolve_run_output_path`,
  `resolve_run_store`, `resolve_project_run_output`, `resolve_entity_output`,
  `run_output_site`), `run_durable_view`.
- `core/compute/retention.py` — the retain verbs (index, inventory, stat, the
  8 MB preview read, forget).
- `core/data/datasets.py` — the data-plane mechanism the mover reuses
  (`register_source`/`fetch`, `FETCH_GUARDRAIL_BYTES`, fingerprints).
- Tests: `tests/test_remote_output_resolution.py` (the invariant guard:
  lookup-never-transfers, digest revalidation, atomic installs, presentation
  parity, the produce-remotely → open-here → settle lifecycle),
  `tests/test_run_durable_view.py`, `tests/test_serving_spine.py`.

## Known gaps

- **Transfer progress is coarse.** `materialize_run_output(progress=)` emits
  phase strings to the launch page; weft's byte-level `transfer.progress`
  events (rate, ETA) aren't surfaced yet.
- **`force=` has no UI affordance.** The override is plumbed end-to-end but no
  surface offers "bring it home anyway" past the guardrail yet.
- **Files-tree aggregate ZIP** (`/api/files/download` on a folder) silently
  omits remote-only files; the run-level archive lists them in-zip — the
  aggregate route should adopt the same manifest honesty.
- **Store bring-back is whole-store.** The data-plane fetches only missing
  blobs, but ABA re-fetches a changed store wholesale into a fresh temp; a
  delta-aware install (reusing the content-addressed cache) would cut repeat
  cost for large, slowly-growing stores.
- **Harvested-store identity is content-derived.** The harvest copy names each
  served file by its truncated sha256 (hardlink when same-device), so identical
  bytes share one store entry across harvests and re-runs, `produced[]` carries
  a real `sha256`, and name→store translation goes through the run manifest
  (the harvested tier here, and `register_dataset`'s manifest fallback for
  bare names written on a remote kernel). Guard: `tests/test_harvest_identity.py`.
- **Freshness digest is `(size, mtime)`, not content.** A same-size in-place
  rewrite whose mtime does *not* advance (a writer that preserves mtime,
  sub-second fs granularity collapsing two writes into one tick, or remote-node
  clock skew) leaves the digest unchanged, so a stale cached copy can serve as
  current. The harvested-artifact tier already content-addresses (`sha256`);
  extending that to the fetched-cache tiers would close it.
- **`match="name"` can resolve a same-basename sibling.** The exec-cwd tier roots
  a basename walk at the exec's cwd and returns the newest-mtime hit; when a run's
  execs share a directory (or ran in the thread scratch dir), a `name` lookup for
  `results.csv` can resolve a *different* exec's same-named file. `match="exact"`
  is unaffected (exact join, no walk); serve/archive/keep use exact.
