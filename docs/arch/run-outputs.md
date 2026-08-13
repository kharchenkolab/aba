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

## The invariant: one locate, one mover — and one notebook

The local-or-remote decision has exactly **one home** —
`locate_run_output(run_id, name, match=, remote=)` — and byte movement has
exactly **one door** — `materialize_run_output(loc, max_bytes=, force=,
progress=)`. Everything else is a thin, named policy over that pair.

**The address index (`core/graph/output_addr`) is the write-time half.** The
locator is keyed on `(run_id, name)`, but the handles that circulate — a name
in chat, a `?path=` in a minted link, a tree node — often carry no run key, and
recovering it by SEARCHING (iterating weft receipts per candidate run at read
time) is what a hard-coded `[:4]` once capped into a 404 for a store that
existed (live 2026-08-08; misc/addressing_and_the_missing_index.md). So the
catalog writes addresses down when it learns them: `_retain_run_outputs`' tail
(keep decisions AND settle — keeper attribution and site in hand) records
`(run, rel) → {name, site, target, kind, bytes, state, ref?, sha256?}`, and the
name→run join reads it O(1). Division of responsibility mirrors weft's own
data-plane doc: weft indexes container→contents (receipts) and
identity→location (its location table, refs); ABA owns meaning→address — this
table. Rows are KNOWLEDGE, not holdings: the serving layers validate bytes on
use, so a stale row costs an honest launch error, never wrong bytes. A
COMPLETE terminal receipt proves absence **in the sandbox only**: it out-votes
a row that merely witnessed sandbox presence (a plain backfill — the
stale-sandbox shadow stays forbidden), but never a recorded KEEP — candidacy
reads weft's retention index (one unfiltered local read; the same truth the
durable view renders from) plus keep/settle-witness rows, so a file kept live
and deleted before kernel stop stays reachable by name (the durable-view-says-
retained / name-door-404 disagreement, live 2026-08-12). `ref`/`sha256` are
nullable adapters for weft's trajectory (hash-under receipts, Fabric epilogue
refs) and ABA never hashes. The name→run search
(`_output_candidacy` → `_confirm_output_matches`) is UNCAPPED — membership is
one batched receipt read; index rows stand in for the ~12 s per-run remote
confirm; confirmed hits BACKFILL, so old projects index themselves by being
used; live kernels (receipt merely missing — a kernel writes its receipt only
at `kernel.stop`) are probed only when nothing recorded answers, since their
absence is re-checkable state with nothing durable to cache. Minted viewer
links carry `&run=` so the launch route resolves by key without any search
(`tests/test_viewer_link_run_key.py`); N-independence — the property the
capped scan violated — is pinned at k=1/7/20 of 20 runs
(`tests/test_output_search_uncapped.py`, `tests/test_output_addr_index.py`).

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

## Harvest honesty (what the tracking contract does when defeated)

The harvest scans the run's working tree within a time window — two things
agent code can do defeat that silently, and both now SAY so instead:

- **Working-directory escape.** Every script lane wraps agent code with a
  cwd probe (prologue records the start dir; epilogue writes start+final to
  a dot-sentinel — `core/exec/run.py` `cwd_probe_*`; the detached node
  harness carries its own inline copy and reports `start_cwd`/`final_cwd`
  in `result.json`). A block that ENDS outside its tree gets a typed warning
  on the result's warning channel naming the valid levers — register the
  absolute path (read-in-place) or write into WORK_DIR; deliberately never
  keep_outputs, which is jobdir-scoped end to end and would keep nothing.
  Persistent (jupyter) kernels also reconcile the session cwd marker from
  the probe each block, so a drifted kernel warns and self-heals rather
  than lying for the rest of the session; weft kernels are excluded (their
  block protocol breaks loudly on chdir).
- **Stale-stamped appearances.** Files that APPEAR during the window but
  carry an older content stamp (archive extraction, `cp -p`, `rsync -t`)
  are counted via the ctime≥window/mtime<window signature and warned about
  — earlier blocks' files fail both clocks and stay silent.
- **Between-block writes.** Kernel lanes harvest from the END of the
  previous harvest, not the block start, so a background writer's files
  attach to the next block instead of vanishing in the gap.

The window compares at whole-second resolution (`_window_floor`). File
stamps and `time.time()` do not share a resolution: BeeGFS/NFS record whole
seconds, so an output written at X.50 stats as X.00 and a step that started
at X.45 sorted its own fresh output BELOW the window — dropping everything
written in the remainder of the start second, with no warning (the stale
counter's ctime is truncated identically, so it could not fire either).
Flooring the window start is the cost-bounded trade: sub-second ordering is
not recoverable from a whole-second stamp, so a file written in the same
second just before the window may be re-caught — tracked beats lost. The
one-shot lanes were never exposed; they take the window from the wrapper
script's own mtime, so both sides already carry the same truncation.

Guard: `tests/test_harvest_honesty.py` (per-lane, red-proven; the
coarse-filesystem drop is simulated by stamping at the whole second, so it
holds on fine-grained filesystems too).

## The consumption surfaces (all through the canonical pair)

| Surface | Entry | Policy |
|---|---|---|
| Serve (run file) | `web/routes/runs.py` `/api/runs/{id}/file` | `resolve_run_file` (exact, small gate) → preview read → honest site-naming 413 |
| Serve (archive) | `/api/runs/{id}/archive` | per-file `resolve_run_file`; skipped files listed in-zip, never dropped |
| Serve (entity / tree) | `main.py` `/api/entities/{id}/download`, `web/routes/files.py` content/raw/download | dangling `/artifacts` cache → run-backed nodes via `resolve_run_file` (`_run_backed_path`) → `resolve_entity_output` → materialize under the small gate, else site-naming 413 / 404 naming the site |
| List | `run_durable_view` / `run_durable_tree` | recorded truth first; two-axis badges (protection × location); `retained` rows always link the live `/file` URL — remote in-place included. A chunked directory store folds to ONE `kind:"store"` row (weakest-live-member state, honest byte sum, member count in the badge — same line the manifest and surface probe hold); runtime bookkeeping (the `blocks/…` transcript + exact root-level runner scaffolding, `_RUNNER_SCAFFOLDING`) folds to a declared `summary.runtime_files` count |
| Tree (Files tab) | `files/tree.py` `build_files_tree` → `_graft_run_outputs` | each run's `output/` comes from the PRODUCED LEDGER (`run_durable_view`: states carried, sandbox-lifetime files marked ephemeral, `cleared` unlisted, cap declared) plus a disk top-up of `artifact_path` for legacy jobdir runs, deduped by rel. Never a bare disk walk: under the kernel substrate produced files live in the kernel workspace and a walk of `artifact_path` finds nothing. Disk grafts are ADDRESS-HONEST: every folder node `_graft_dir` creates carries its real on-disk path — a directory-shaped source (a store mirror fetched into `work/<run>-fetched/`) must resolve and serve locally, never shadow a launch as an address-less basename match; and the launch-route resolver (`web/routes/viewers.py` `_resolve_files_node`) treats a node with neither a run key nor any byte address as NON-TERMINAL, falling through to the run-output resolver instead of starving both launcher arms (found live: a stale fetched mirror 404'd an otherwise-streamable store) |
| Export (zip / materialize) | `/api/files/download`, `materialize_tree(resolve=)` | run-backed nodes resolve through the caller-supplied run resolver; files the tree lists but this machine can't serve are NAMED (`SKIPPED-FILES.txt` / `missing`+warning), never silently omitted |
| Register (`register_dataset`) | `curation._resolve_dataset_path` | `locate_run_output(active_run, name)` **first** (site- and stopped-kernel-aware); the ranked scratch scan is the fallback and the only tier for no-run registrations; the durable `run_key` is captured via the resolver (`_capture_run_key`), site-agnostically. Durable-home lanes (in-place outside aba's trees, and remote site paths) eagerly mint + record the data-plane content `ref` under the transfer-guardrail byte budget (best-effort — a mint failure leaves ref absent and the record stands; the viewer launch mints lazily): the recorded ref is what lets the range channel's ref arm stream the dataset with no resolvable run |
| Search (`find_files`) | `project_locate.locate_project_files` | every tier answers `durability`; a live-sandbox hit says it is swept and must be registered/copied before reuse — silence is a claim |
| View | `viewers` routes + `get_viewer_url` + external launcher `_resolve_source` | a raw ABSOLUTE path is reverse-looked-up to a registered dataset FIRST (`data_location.entity_for_path`, recorded metadata only — no probe; newest live match wins, relative inputs excluded so a verbatim-recorded relative string can't steal a tree node's resolution), so a byte-identical home resolves entity-backed (the mirror lever works); otherwise lookup (`resolve_project_run_output`) returns a **remote marker**, moving nothing. Both branches carry a location pre-flight note (site, size, the honest lever per source) minted from recorded facts — with ONE shared stream-or-fetch decision (`_remote_note`: on a range-capable substrate it additionally probes streaming readiness, so a store that will stream says so instead of warning about a whole fetch) — and an absolute-path miss names the remote levers rather than reporting a bare "no file matching"; launch calls `resolve_run_store` (guardrail budget, progress, retain-on-view) — EXCEPT a remote directory store on a range-capable substrate, which **streams** (see below) instead of materializing. A launch whose EVERY tier misses still tells the truth: when the entity's recorded facts say the bytes are a by-reference remote home with NO data-plane ref recorded, the terminal error names the site in the same shape as the run-keyed raise (`_source_not_found`, the honesty bridge) so the launch page's mirror lever engages even with the producing run unresolvable — entity facts only, no probe, no new tier; non-by-reference sources keep the exact generic wording |
| View (ranged read) | store route `main.py` `_parse_byte_range` + `range_cache.serve_remote_range` | a request carrying `Range:` is served from a **1 MiB segment grid** instead of the whole member — the difference between ~84 KB and 176 MB for one gene column, on a channel measured BANDWIDTH-bound (~0.85 s fixed per call, 1.2-2.8 MB/s marginal; `misc/from-aba-first-touch-cost.md`). The interval's covering segments are fetched, cached one file per index under `<cache>/.seg/<member>/<i>`, and sliced; the member's size (needed for `Content-Range`, and the only bound on an open-ended `bytes=N-`) comes from a reply and is kept in a `.size` sidecar, so a range wholly inside cached segments costs ZERO substrate calls. Contiguous MISSING runs are coalesced into calls of up to `RANGE_CAP`, so a whole-member walk costs the same round trips it always did — a fixed grid without coalescing would turn one 16 MiB call into sixteen and be a large regression. Segments are installed only when provably WHOLE, buffered ACROSS replies (a reply is not a segment: the substrate clamps at its own cap, which may be smaller than the grid — installing only what one reply covered meant such a substrate could never complete a segment and every range refused). **The splice guard:** covering-segment presence is re-checked AFTER the fetch, because segments are evictable and a back-haul can half-fail; a range that cannot be fully covered returns 502 rather than concatenating what happens to be on disk — the 16 MiB short-read failure one level down. Un-ranged requests, a member already cached WHOLE (`serve_remote_range` returns None so the route falls through), and any header the parser does not understand all keep the whole-member path byte-for-byte; a malformed `Range` degrades to 200 rather than 400, since the whole member always answers a range request correctly |
| View (store contract) | `pagoda3.py` `_viewer_contract` / `_repair_viewer_store` | a viewer store carries the count payload in BOTH orientations, and lstar >=0.2.2 makes the gene-major (csc) basis a hard requirement — the pinned pagoda3 dist checks the same `provenance.viewer="basis"` stamp client-side, so a store written earlier is REFUSED in the browser rather than merely degraded. Each locally-served lane therefore probes the basis and repairs when it is cell-major only. The probe is a metadata-only subprocess in the session env (`lstar.read(lazy=True)` + the basis rule) — deliberately NOT `lstar.validate()`, which touches field values and so both materializes a lazy store and mis-reports on one — and its verdict is TRI-STATE: `None` (no lstar, unreadable store) means do nothing, never repair on a guess. Repair is lstar's own `lstar viewer <store>` in place, and only ever on bytes ABA owns. Lane by lane: **convert** self-heals (`ensure_derived` keys on the lstar-sc version, so a pin bump re-converts) and raises naming the SESSION ENV if its output is still cell-major — the legible form of a deployment whose pack never moved; **zip** re-derives on that same key but re-derives by unpacking the same stale bytes, so it repairs the extraction (never the archive) and raises if the repair does not take; **native directory** is symlinked and never rebuilt at all, so a defective store is COPIED into the served dir and the copy repaired — `real` is a run's retained output in weft's workspace, and opening a file in a viewer is not a licence to rewrite it. The copy is conditional, so only a legacy store pays it, once. A store served by the range channel cannot be repaired from here at all — its bytes are a run's output on another machine — so it is REPORTED instead, on the pre-flight note (`tools/viewers.py` `_stream_contract_note`, riding the home `_remote_stream_ready` already resolved rather than paying for a second round trip). The verdict comes from two small metadata reads through the row's own arm (`remote_viewer_contract`): the store root carries `profiles` + the field list, the basis field's metadata carries its `encoding` — no lstar on the far side, no materialization, measured at 103 KB + a few hundred bytes against a real store. The note names the fix (`lstar viewer <store>` on the site) and REPLACES the "nothing to do: just open it" reassurance, which is false when the viewer is about to refuse. Deliberately NOT checked at launch: the ref arm registers from recorded facts with no round trip, and two reads in front of every streamed open would tax the common case for a rare defect |
| View (remote store, streamed) | store route `main.py` `/pagoda3-store` + `core/viewers/range_cache.py` | the range channel: a directory store whose bytes live on another site serves its chunks on demand — NO whole-store fetch, so the 2 GiB guardrail never engages. A registry row addresses the bytes by ONE of two arms. **Run arm** — `{target, base_rel, site, size, digest}`: the launcher resolves the producing run's remote store home (`resolve_remote_store_stream`, over the canonical `locate_run_output` + inventory-derived sandbox rel). **Ref arm** — `{ref, site, size, digest}`: an entity-backed by-reference REMOTE directory store whose recorded `metadata.ref` is a data-plane content ref registers addressed by that ref — RECORDED FACTS ONLY (no inventory round-trip), so it streams with NO resolvable run required (`_register_ref_arm`, tried first; the run arm is the fallback). Eligibility is ONE shared predicate (`ref_stream_facts`, pagoda3.py) consumed by BOTH the launcher and the pre-flight note — store-suffix name, recorded ref OR a mintable durable home (ref absent but `home.path`/`ref_path` recorded: the launcher mints the ref at click via `_mint_dataset_ref` and persists it single-key; the note may promise on the same facts), `dataset_location` remote + by-reference, AND recorded directory shape (descriptor/fingerprint `n_files >= 2`; a FILE fingerprints as 1) — so the note can never promise a stream the launcher would decline, and a FILE-shaped ref wearing the store suffix refuses to the materialize path instead of stream-registering into a mute-404 viewer (the substrate refuses rel-on-FILE). Either arm registers `store_key → {arm addressing}` in a restart-surviving per-project registry and mints the SAME store URL without materializing (ref-arm `store_key` is stable per content ref). The store route serves a local file byte-identically (ceiling); on a local miss it consults the registry and serves the requested chunk file from a per-chunk cache, back-hauling only touched chunks over that arm's verb — run arm `retention.file_read_range(target, base_rel + chunk_rel)`, ref arm `retention.data_read_range(ref, rel=chunk_rel, site=)` (the ref IS the tree root, so the chunk rel passes through as the member rel, no base_rel join). A miss serves the TARGET synchronously on the singular one-shot lane (nothing sits between the browser and its chunk — a synchronous neighborhood batch was measured to inflate first-touch latency 3-10x by paying its transfer before answering) and then fires a BACKGROUND batched prefetch (`rels=[...]`, daemon thread, deduped per seed): up to `PREFETCH_SIBLINGS` guessed chunk-grid siblings (`_numeric_siblings` — trailing-integer path segments advanced), bounded by a `BoundedSemaphore` on concurrent prefetch threads (each drives the substrate on its OWN thread — `sync_call` runs on the caller, bypassing the adapter pool — so unbounded spawning opened unbounded site sessions and starved the foreground reads) and by `PREFETCH_BATCH_MAX_BYTES` against the recorded member size (the batch asks for WHOLE members and holds the reply resident, while `RANGE_CAP` clamps only the singular lane), warm the cache ahead of the browser's grid walk; wrong guesses are typed per-entry errors costing nothing, budget-deferred `not_read` rels are abandoned (future misses), and a substrate whose verb predates `rels=` (TypeError, noted once per process) simply never prefetches. Coverage honesty: coordinate guessing warms GRID-shaped arrays only — a store shaped as many single-chunk arrays (the live-measured case) gets no prefetch hits; manifest-ORDER prefetch (next members in tree-listing order, covering both shapes) awaits a substrate member-listing verb (requested — `data_stat` exposes counts, not names). Weft's retryable `internal.error` (marker-less probe) is retried exactly once (`_retry_once`) on both lanes. Identical envelope + error mapping across arms. Cache install is atomic (temp → `os.replace`), size-capped (mtime LRU), wiped when the store's freshness digest changes. Typed `data.missing` → 404; backhaul/adapter failure → 502 naming the site; `..` URL → 403 before any backhaul. Served chunk responses carry `Cache-Control: private, max-age=86400, immutable` for REF-arm stores (content-addressed store_key — a byte change mints a new URL; `private` because these are project bytes behind the deployment's proxy, and a DAY rather than a year because the ref is minted once over an external home ABA does not own, so an in-place regeneration keeps the URL and no server-side wipe can reach a longer pin) and `no-cache` revalidation for RUN-arm stores (their key survives re-derives, so a browser must not pin stale chunks). Each verb is probed independently (`range_read_available(verb)`, cached per verb) — a deployment may expose the run verb but not the ref verb; a verb ABSENT → its arm degrades to the materialize path above, byte-identical to today |
| View (remote artifact, inline) | `entity_ops._fetch_remote_view_file` | `view_artifact` needs bytes on THIS filesystem, so a local-resolution miss asks the Run graph before refusing: a path naming a REMOTE run output is pulled over the PREVIEW channel (`read_run_file` → weft `run_file_read`, base64, 8 MB cap) and cached under the project's artifacts area (`_remote_view/`, atomic install) so a re-view is free. Deliberately the preview channel, not transport — viewable artifacts are small, and a file past the cap is REFUSED with the dataset/mirror lever rather than rendered from a truncated read (half a PNG is not a figure). Before this the tool had no site-aware branch at all and refused such paths, so the workaround was submitting a whole extra remote job just to copy the file into the harvest path |
| Render | cards / `metadata.run.sites` / exec `compute` block | reads recorded placement only; never a live stat |

Site literals in the addressing surface are census-guarded
(`tests/test_path_resolution.py`): every `site == "local"` comparison is either
a rationale-annotated allowlist entry or a failing guard — resolution logic may
not be re-derived at a door (misc/paths.md owns the rationale).

## Key implementation references

- `content/bio/lifecycle/runs.py` — `locate_run_output`, `materialize_run_output`
  (+ `_materialize_file` / `_materialize_store`, `_store_members`, stamps), the
  policy shims (`resolve_run_file`, `resolve_output`, `resolve_run_output_path`,
  `resolve_run_store`, `resolve_project_run_output` / `…_located` (the latter
  also returns site/size/locality for the viewer pre-flight note, no second
  probe), `resolve_entity_output`, `run_output_site`), `run_durable_view`.
- `content/bio/data_location.py` — `entity_for_path` (path→dataset reverse
  lookup, recorded metadata only) backs the viewer link's instant entity-backed
  resolution of a raw registered home; `content/bio/tools/viewers.py` +
  `core/viewers/launch_page.py` carry the location pre-flight + the path-backed
  remote-failure guidance.
- `core/compute/retention.py` — the retain verbs (index, inventory, stat, the
  8 MB preview read, forget) + the two ranged-read doorways backing the range
  channel: `file_read_range` (run-keyed) and `data_read_range` (ref-addressed —
  the ref arm), sharing the per-verb probe `range_read_available(verb)`.
- `core/viewers/range_cache.py` — the range channel's serving layer (domain-
  neutral): the per-project remote-store registry (two arms per row, exactly one
  each) + whole-chunk-file cache + `serve_remote_chunk` (arm-agnostic assembly
  via `_chunk_reader`). `content/bio/lifecycle/runs.py` `resolve_remote_store_stream`
  is the run arm's home-`{target, site, store_rel}` resolver;
  `content/bio/viewers/launchers/pagoda3.py` `_register_remote_stream` registers +
  mints the stream URL (`_register_ref_arm` is the ref arm, tried first); the
  store route branch lives in `main.py` `pagoda3_store`.
- `core/data/datasets.py` — the data-plane mechanism the mover reuses
  (`register_source`/`fetch`, `FETCH_GUARDRAIL_BYTES`, fingerprints).
- `content/bio/files/tree.py` — `_graft_run_outputs` (the ledger-sourced
  `output/` graft), `web/routes/files.py` `_run_backed_path` (serve fallback
  for ledger-sourced nodes).
- Tests: `tests/test_remote_output_resolution.py` (the invariant guard:
  lookup-never-transfers, digest revalidation, atomic installs, presentation
  parity, the produce-remotely → open-here → settle lifecycle),
  `tests/test_run_durable_view.py`, `tests/test_serving_spine.py`,
  `tests/test_output_door_census.py` (every lister/server of run outputs
  reads the ledger — the door census), `tests/test_range_channel.py` (the range
  channel, BOTH arms: cache miss back-hauls / hit short-circuits — armed; typed
  data.missing → 404, backhaul → 502, traversal → reject before any read;
  per-verb degradation matrix; ref-arm rel pass-through with the run arm's
  `data_read_range` sentinel un-dispatched; the production shapes end-to-end
  (ref recorded → streams with the mint seam untouched; ref absent + home
  recorded → launch MINTS, persists the single key, streams); mint failure →
  honesty bridge with NO metadata write; identity-less shapes never touch the
  data plane; FILE-shaped / shape-unconfirmed refs refused; a mis-shaped ref in
  the registry 404s every chunk and caches nothing; the note↔launcher agreement
  matrix (mintable included, mint counted); local-branch ceiling — all
  red-proven).

## Known gaps

- **Driver machinery is advertised as a Run output, and it serves empty.** The
  surface oracle fails `cheminformatics` deterministically (2/2, 2026-08-13) on
  `surface:empty_bytes:<rid>/blocks/0001.err` — the durable view lists
  `blocks/0001.err` with a truthy `bytes`, and `/api/runs/<rid>/file?rel=…`
  returns 200 with no content. `blocks/NNNN.err` is the weft block driver's own
  stderr, empty precisely because the step succeeded, so the row is wrong twice:
  it is not the user's output, and its advertised size is not its served size.
  This is the recurrence of the harvest-filter class already on record (a fake
  sandbox that started EMPTY blessed a filter which scooped `current_block` as a
  Run's only output) — a real jobdir always carries the driver's machinery, so
  the filter must exclude it by rule rather than by what a fixture happens to
  contain.
- **A working file is registered as a Dataset instead of kept.**
  `ephemeral_deliverable` fails 3/3 (2026-08-13) with
  `tool_not_used:keep_outputs` + `tool_called_too_often:register_dataset (1 > 0)`:
  the agent writes the file in a kernel and then mints a Dataset entity, which
  the scenario names as the wrong lane ("Datasets are first-class scientific
  entities, not retained working files"). Deterministic, same tool set each run.
  Its baseline is a SINGLE accepted sample from 2026-07-23 on a different
  machine, so the honest statement is "current behaviour differs from one
  three-week-old observation", not "today's change broke it".
- **The streamed store's contract verdict is coarse by design.** The remote
  probe answers only for the shape metadata alone can settle — a
  `viewer@0.1` store whose basis is the field named `counts` — and returns
  UNKNOWN for a stamped or differently-named basis, which lstar's own rule
  would resolve. UNKNOWN is the common answer and is deliberately silent;
  the alternative (guessing) produces a warning nobody can act on about a
  store ABA cannot repair. A public metadata-only check from lstar would
  close this properly.
- **ABA mirrors lstar's basis-selection rule.** `_viewer_contract` re-implements
  `validate._viewer_basis` because lstar exposes no metadata-only contract
  check — `validate()` reads field values. A cross-repo mirror drifts
  silently; the ask is filed (`misc/from-aba-viewer-contract.md`) and the
  mirror is meant to be deleted, not maintained.
- **Transfer progress is coarse.** `materialize_run_output(progress=)` emits
  phase strings to the launch page; weft's byte-level `transfer.progress`
  events (rate, ETA) aren't surfaced yet.
- **`force=` has no UI affordance.** The override is plumbed end-to-end but no
  surface offers "bring it home anyway" past the guardrail yet.
- **Files-tab durable states don't refresh.** The tree is built per fetch;
  a state flip (saving → retained) shows on the next tab load, not live —
  the Run card's polled panel is the live surface.
- **Out-of-band env installs are tripwired at snapshot, not at install
  time.** An in-code package install (subprocess pip) mutates the live
  session prefix without touching the registry; the snapshot dirty-cache
  now keys on a cheap prefix signature, records the event as an
  `out-of-band` addition, and re-snapshots so the frozen identity stays
  true (`core/compute/project_env.py` `_prefix_signature`; replay skips
  marker rows). The residual gap: identity claims between the rogue
  install and the next snapshot still serve the stale id.
- **Store bring-back is whole-store.** The data-plane fetches only missing
  blobs, but ABA re-fetches a changed store wholesale into a fresh temp; a
  delta-aware install (reusing the content-addressed cache) would cut repeat
  cost for large, slowly-growing stores.
- **The range channel is Phase 1 (chunk streaming only).** A remote directory
  store streams read-only through the store route's per-chunk cache; there is no
  tunneled colocated range server, no multi-range request, and no cache→mirror
  promotion. A streamed store has no local `store_path`, so the single-file
  `.lstar.zarr.zip` download returns a clean 409 until a mirror brings the tree
  home. Freshness is coarse (a re-derive is caught only when the store's digest
  changes at the next launch-time registration, which wipes the stale per-chunk
  cache; there is no per-chunk revalidation). **Nested-store streaming falls back
  to materialize:** the home resolver derives the store's sandbox rel with the
  SAME leading-segment rule as the store gate (`_rel_under_store`), so a store
  whose members appear only under a subdirectory (`<subdir>/<name>/…`) is never
  registered for streaming — deliberate: a looser matcher could bind the registry
  to a colliding interior path whose digest/size the gate never measured. The
  pre-flight note's stream-or-fetch decision is shared by both branches
  (`_remote_note`); the ref arm answers through the SAME eligibility predicate
  the launcher registers by (`ref_stream_facts` — an agreement-matrix guard
  keeps note verdict == launcher verdict per entity shape) from recorded entity
  facts with NO round-trip, while the run arm's decision costs one remote-tier
  resolve inside the link-mint call when the verb is live — see the doorway's
  docstring. A run-unresolvable by-reference remote source (run entity deleted,
  target-less exec, keeps forgotten) now STREAMS through the ref arm: its
  registration either recorded the data-plane content ref (durable-home lanes
  mint it eagerly under the transfer-guardrail byte budget, best-effort —
  `register_source(eager_ref_max_bytes=…)`), or the launcher MINTS it at click
  (`_mint_dataset_ref`: the same `data_register(path, site=, ingest=False)`
  call, in the async prepare job — an on-site read pass, seconds-scale;
  persisted via `patch_metadata` single-key, no write on failure). The one
  accepted note↔launcher divergence: a mint FAILURE at click degrades that
  launch to the materialize/bridge path after the note promised streaming —
  mirror lever unaffected. The residual unstreamable shapes are **identity-less
  registrations** (ref absent AND no recorded home/ref path to mint from — its
  launch bridges to the remote-flavored terminal error and the mirror lever
  engages; a transient mint failure lands here for that launch and retries on
  the next), **shape-unconfirmed refs** (no recorded descriptor/fingerprint
  `n_files >= 2` — deliberately refused to the materialize path, which handles
  both file and tree shapes, rather than risk a mute-404 stream), and the
  **nested-store** case above. Each verb is probed
  independently; on a substrate that lacks `run_file_read_range` the run arm is
  dormant, and one that lacks `data_read_range` leaves the ref arm dormant —
  in both cases behavior is byte-identical to the pre-channel materialize path.
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

## The project-wide name door (`content/bio/project_locate.py`)

Agent-facing contract, in full: **refer to files by the name your code used;
the platform finds them.** `locate_project_files(pattern)` is the single
project-scoped name→file resolver; every name-based agent surface routes
through it — `find_files` (whose storage-root parameter is gone), the
read-path anchor fallback in `_resolve_project_path` (bare names that don't
anchor), and `register_dataset`'s bare-name tail. The walk is
platform-internal, over the custody chain: live kernel sandboxes first (local
jobdirs walked; remote kernels matched against the per-turn inventory the
scrape already holds — a lookup never moves bytes), then run manifests across
recent executions (including link-only rows: over-cap and skipped-shape
outputs that never came home keep their names real), then user data and
scratch trees.

Honesty rules, each guarded (`tests/test_project_locate.py`):

- **Bounds are declared on hits and misses** — a bounded search that doesn't
  state its coverage reads as exhaustive (the silent-truncation class).
- **Unreachable tiers are UNKNOWN, never absent** — a manifest-known file on
  a dead site is still listed, marked unavailable.
- **Collisions return labeled candidates** (producing run, tier, locality) —
  never a silent newest-wins. Identity stays `(run, rel)` / content digest;
  names are queries.
- **Every hit names its tier and what opening costs** ("fetches from <site>
  on open") — affordances, not architecture, so the agent needs no storage
  model.
- **No private tree-walks**: a guard enumerates every `os.walk`/`rglob` in
  the agent-tools layer against a rationale-annotated allowlist (the door,
  the run-scoped resolver it delegates to, and listing enumerators) — a new
  hand-rolled walker fails with its location.

**Known gaps.** Listing enumerators (data-file listing, the orientation
banner's tree) still walk their own dirs rather than sourcing from the door;
the recorded-scenario coverage (`file_lookup`) enters the accepted baseline
only after the next provisioned sweep; recipe-pack idioms ship separately via
the bundle update.
