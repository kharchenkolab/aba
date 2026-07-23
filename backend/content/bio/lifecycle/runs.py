"""Analysis-run lifecycle (entity-model v3 'Analysis run').

A Run groups the outputs of a coherent, usually planned, analysis so they read
as one unit instead of scattering across per-turn `analysis` entities. A Run IS
an `analysis` entity (files/tree.py RUN_TYPES = {"analysis"}) tagged in metadata:

    thread_id:  <home thread>     — so the Files tree places it under the thread
    run_state:  "open" | "closed"

At most one Run is *open* per thread — the "active" one. While it's open,
harvested artifacts (lifecycle/registry._ensure_analysis) attach to it, so a
multi-turn pipeline is one Run with its figures/tables rather than a pile of
per-turn analyses.

Opened by the agent (open_run) as it begins executing an approved plan, or
rotated by the next open_run. Closed by close_run (explicit, or on a topic
pivot the agent recognizes). Closing an EMPTY Run discards it, so an abandoned
or re-planned analysis doesn't litter the tree.

Stage 4 (misc/exec_records_and_versioning.md): `close_idle_runs` adds the
auto-close on inactivity, and `materialize_run_from_ambient` promotes the
auto-created ambient analysis to a properly-titled Run when the user
retroactively pins a casual-chat artifact.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from core.graph._schema import _conn, WORKSPACE_ID
from core.graph.entities import (
    create_entity, get_entity, update_entity, archive_entity, list_entities,
)

_log = logging.getLogger(__name__)

# Stage 4: a Run with no entity-write activity for this long auto-closes.
# The signal we use is `entities.updated_at` on the Run row, which bumps
# every time a child artifact lands, the output manifest refreshes, or
# code is appended — i.e., on real harvested work. A purely-reading user
# (just scrolling the Run's outputs) gets no bumps, so their Run will
# close cleanly after the timeout. The next tool call in the thread
# auto-creates a new ambient analysis (via _ensure_analysis), so there's
# no lost-state failure mode when the timeout fires.
IDLE_TIMEOUT_S = 1800   # 30 minutes


def record_weft_target(run_id: Optional[str], target: Optional[str]) -> None:
    """Persist a weft target (a kernel_id / job_id that produced this Run's
    outputs) onto the Run entity's `metadata.weft_targets`. This is the handle
    retention needs: after a kernel stops, aba names the target to call
    run_inventory/run_retain/run_forget (the run id itself is the retain `label`).
    Best-effort, dedup, never raises — a missing target just means no retention
    for that run. See misc/output_durability.md §A2."""
    if not run_id or not target:
        return
    try:
        ent = get_entity(run_id)
        if not ent:
            return
        targets = list((ent.get("metadata") or {}).get("weft_targets") or [])
        if target in targets:
            return                    # best-effort dedup; readers dedupe too
        # atomic list APPEND — the read-then-set shape lost one of two
        # concurrent appends (both readers saw the same list); '[#]' insert
        # closes the window, and duplicate-under-race is read-side deduped
        from core.graph.entities import append_metadata_list
        append_metadata_list(run_id, "weft_targets", target)
    except Exception:  # noqa: BLE001 — retention bookkeeping must never break a run
        pass


def active_run_id(thread_id: str) -> Optional[str]:
    """The currently-open Run for this thread, or None (newest wins)."""
    if not thread_id:
        return None
    # list_entities orders by created_at asc → reverse for newest-first.
    for e in reversed(list_entities(type_filter="analysis", include_archived=False)):
        md = e.get("metadata") or {}
        if md.get("thread_id") == thread_id and md.get("run_state") == "open":
            return e["id"]
    return None


def run_id_for_plan(plan_entity_id: Optional[str]) -> Optional[str]:
    """The Run opened for a given plan (its metadata.plan_entity_id matches), newest-first.
    Used at plan-complete, where only the plan id is in scope (guide dispatches on_plan_complete
    with just plan_entity_id) — so retention can find the Run to retain at plan-end."""
    if not plan_entity_id:
        return None
    for e in reversed(list_entities(type_filter="analysis", include_archived=False)):
        md = e.get("metadata") or {}
        if md.get("plan_entity_id") == plan_entity_id:
            return e["id"]
    return None


def agent_actor_for_thread(thread_id: Optional[str]) -> Optional[str]:
    """agent:<run_id> for the open run on a thread — the actor for an agent-tool
    create on the gateway thread, where the ambient actor contextvar can't reach
    (modularity_audit2 §2B). None if no open run."""
    from core.graph.derivation import agent_actor
    rid = active_run_id(thread_id) if thread_id else None
    return agent_actor(rid) if rid else None


def _has_children(run_id: str) -> bool:
    from core.graph.entities import exists_entity   # P3.1: store read API, not raw SQL
    return exists_entity(parent_entity_id=run_id, status_not="archived")


def run_output_dir(project_id: str, run_id: str) -> "Path":
    """The scratch working directory a Run's pipeline writes into — every file
    it produces (figures, .rds/.h5ad, subfolders) lands here, so the Run is a
    browsable output bundle. Keyed by the run's entity id."""
    from core.data.workspace import scratch_dir
    return scratch_dir(str(project_id or "default"), str(run_id))


def open_run(thread_id: str, title: str, *, focus_entity_id: Optional[str] = None,
             plan_entity_id: Optional[str] = None, scenario_of: Optional[str] = None) -> str:
    """Open a Run for the thread, rotating out any currently-open one first
    (a new boundary supersedes the previous Run). Returns the run (analysis) id.
    Records the run's output directory as its `artifact_path` so run_python/run_r
    cd into it and the Files tree can show its full output.

    Idempotent against redundant AGENT calls: agents frequently call open_run at the
    start of an approved-plan execution even though a Run was already opened (server-
    side, on plan approval) — sometimes twice, incl. a blank-title call. Without this,
    each rotates (close+recreate), churning Runs and leaving outputs under a junk Run.
    So for an agent call (no plan_entity_id) when a Run is already open, a redundant
    request — no/blank title, or the same title as the open Run — returns the existing
    Run instead of rotating. The server's plan-boundary call (plan_entity_id set) always
    rotates, as a new approved plan should supersede the prior Run."""
    # Stage 4: opportunistic sweep of OTHER threads' idle Runs. Cheap (only
    # touches open-state rows) + no extra scheduler needed. Plan-Go is the
    # canonical user-attention boundary, so sweeping here matches the
    # design "new plan starts in same thread → old Run auto-closes" — and
    # we extend it free to "and idle Runs in other threads close too".
    try:
        close_idle_runs()
    except Exception as e:  # noqa: BLE001 — sweep failure must not block opening
        _log.warning("open_run: opportunistic close_idle_runs failed: %s", e)
    cur = active_run_id(thread_id)
    if cur and not plan_entity_id:
        norm = (title or "").strip()
        if not norm:
            return cur   # blank-title open_run → never mint a junk duplicate
        cur_title = (get_entity(cur) or {}).get("title") or ""
        if norm[:120] == cur_title[:120]:
            return cur   # same title as the already-open Run → no rotation/dup
    close_run(thread_id)
    md: dict = {"thread_id": thread_id, "run_state": "open", "origin": "internal"}
    if plan_entity_id:
        md["plan_entity_id"] = plan_entity_id
    # §8f (more_weft_ui.md): a re-run WITH CHANGES branches — the new Run carries
    # a `scenario_of` edge to the original, so the baseline keeps its history and
    # the variant reads as a sibling. A plain re-run (as-is) passes no baseline.
    from core.graph.derivation import manual
    rid = create_entity(
        entity_type="analysis",
        title=(title or "Analysis run").strip()[:120],
        parent_entity_id=focus_entity_id or WORKSPACE_ID,
        derivation=manual(),   # Phase 2B: a Run is opened, not derived (actor from ambient)
        scenario_of=scenario_of or None,
        metadata=md,
    )
    try:
        from core import projects
        d = run_output_dir(projects.current() or "default", rid)
        update_entity(rid, artifact_path=str(d))
    except Exception:  # noqa: BLE001 — never block opening a run on dir setup
        pass
    # Force the path-orientation preamble on the next run_python /
    # run_r call in this thread. The cwd is about to shift into the
    # new Run's output dir, but the kernel hasn't been touched yet —
    # without this nudge the existing _ensure_kernel_cwd hook would
    # still fire on the next call, BUT only if the agent makes a
    # run_python/run_r call in this turn. The flag is harmless if the
    # agent doesn't run code yet; it just primes the next invocation.
    # prj_a6f40e94 2026-06-19: 1 preamble in 37 tool_results,
    # straight into "cannot open the connection" loop.
    try:
        from core.exec.kernels import get_pool
        pool = get_pool()
        for lang in ("python", "r"):
            sess = pool.peek(thread_id, lang)
            if sess is None:
                continue
            sess._aba_cwd_just_switched = "RUN_OPEN"
    except Exception:                                            # noqa: BLE001
        pass
    return rid


def open_imported_run(thread_id: Optional[str], title: str, source_dir: str, *,
                      pipeline: Optional[str] = None, revision: Optional[str] = None,
                      source: Optional[str] = None,
                      focus_entity_id: Optional[str] = None) -> str:
    """Create a Run (analysis entity) that REFERENCES an external, read-only results dir produced
    outside ABA (misc/external_import.md). Returns the run id.

    The Run's `artifact_path` points at `source_dir` so the manifest browses the whole tree with
    zero copy; `by_reference`/`ref_path`/`import_fingerprint` mark it external and give drift a
    baseline. All of this lives in the entity sidecar (Location 2), so a DB-crash recovery
    reconstructs the imported Run fully even if the external dir is offline. ABA never writes to
    `source_dir`. Created `run_state="closed"` (a completed import — it doesn't hijack the thread's
    active Run); harvested children still attach via the explicit analysis_id passed to the job."""
    from core.graph.derivation import imported
    from core.data.external_ref import fingerprint
    md: dict = {"thread_id": thread_id, "run_state": "closed", "origin": "external",
                "by_reference": True, "ref_path": str(source_dir),
                "pipeline": pipeline, "revision": revision,
                "source": source or "external"}
    try:
        md["import_fingerprint"] = fingerprint(str(source_dir))
    except Exception:  # noqa: BLE001 — no baseline just means no drift detection
        pass
    rid = create_entity(
        entity_type="analysis",
        title=(title or "Imported run").strip()[:120],
        parent_entity_id=focus_entity_id or WORKSPACE_ID,
        derivation=imported(source or "external"),
        metadata=md,
    )
    try:
        update_entity(rid, artifact_path=str(source_dir))
    except Exception:  # noqa: BLE001
        pass
    return rid


def close_run(thread_id: str) -> Optional[str]:
    """Close the thread's open Run, if any. An EMPTY Run (no outputs, no
    captured exec records) is discarded instead of kept, so abandoned/
    re-planned analyses don't litter the tree. Returns the closed/discarded
    id, or None.

    Post-cutover: "captured code" is sourced from the Run's exec records
    (aggregated_code_for_run), not the legacy entity.producing_code.

    Option B / Phase 6: when the Run was opened via a plan-Go (its
    metadata carries a plan_entity_id), inspect that plan's declared
    expected_outputs and auto-pin matching artifacts so the Results
    rail reflects the plan's stated deliverables. Intermediate
    artifacts that don't appear in any step's expected_outputs stay
    unpinned (the user can still pin them manually). This runs
    BEFORE the state flip so the auto-pin gets `run_state=open`
    semantics for entity-creation hooks, then we close.
    """
    rid = active_run_id(thread_id)
    if not rid:
        return None
    from core.graph.exec_records import aggregated_code_for_run as _agg_code
    if not _agg_code(rid) and not _has_children(rid):
        archive_entity(rid)
        return rid
    ent = get_entity(rid)
    md = dict((ent or {}).get("metadata") or {})
    # Auto-pin declared finals BEFORE flipping run_state to closed.
    _auto_pin_declared_finals(rid, md)
    # Durably retain this Run's produced files against its weft target(s) — a
    # deferred pin on the live session kernel, settled at the kernel's own stop
    # (we do NOT stop the shared kernel; misc/output_durability.md §6.3).
    _retain_run_outputs(rid, md)
    from core.graph.entities import patch_metadata
    # Failure visibility (found live: a run whose only step raised rendered
    # with NO failure indication on its card — the thread showed the error,
    # the entity card claimed nothing): count failed exec records at close
    # and stamp them into run metadata, so the card (and the agent's
    # run_outputs_summary projection) can say "closed, N failed steps".
    try:
        from core.graph.exec_records import list_by_run as _lbr
        n_failed = sum(1 for r in _lbr(rid)
                       if (r.get("status") or "") not in ("ok", ""))
        if n_failed:
            cur = get_entity(rid)   # fresh — retain/auto-pin patch run md too
            run_md = dict(((cur or {}).get("metadata") or {}).get("run") or {})
            run_md["failed_steps"] = n_failed
            patch_metadata(rid, {"run": run_md})
    except Exception:  # noqa: BLE001 — the stamp must never block a close
        pass
    patch_metadata(rid, {"run_state": "closed"})   # single-key: no blob race
    return rid


def note_run_site(run_id: Optional[str], site: Optional[str]) -> None:
    """Record WHERE a step of this Run executed (metadata.run.sites, deduped;
    'local' is the default story so only REMOTE placement is recorded). The
    Run card's §8d verdict renders placement from this — the legacy
    executor:'remote-hpc' marker died with the retired sbatch lane, leaving
    every remote run's card claiming 'ran locally' (found live by the
    browser UI study). Best-effort, never raises."""
    if not run_id or not site or site == "local":
        return
    try:
        ent = get_entity(run_id)
        if not ent:
            return
        sites = list(((ent.get("metadata") or {}).get("run") or {})
                     .get("sites") or [])
        if site in sites:
            return                     # best-effort dedup; readers dedupe too
        # atomic nested APPEND — whole-`run` RMW here raced the manifest
        # writer on the same key and either could silently drop the other
        # (recheck-confirmed); '[#]' insert has no read-modify-write window
        from core.graph.entities import append_metadata_list
        append_metadata_list(run_id, "run.sites", site)
    except Exception:  # noqa: BLE001 — placement note must never break a run
        pass


def _declared_output_names(run_metadata: dict) -> set:
    """Filename-like `expected_outputs` declared across the plan that opened this Run
    (recipe `produces:`) — as basenames. The §6 rank-1 "declared" keep signal. Empty if
    the Run wasn't plan-opened; bare descriptions (no '.') are skipped."""
    plan_id = run_metadata.get("plan_entity_id")
    if not plan_id:
        return set()
    plan_ent = get_entity(plan_id)
    if not plan_ent:
        return set()
    names: set = set()
    for step in (plan_ent.get("metadata") or {}).get("steps") or []:
        if not isinstance(step, dict):
            continue
        for o in (step.get("expected_outputs") or []):
            if isinstance(o, str):
                leaf = o.rsplit("/", 1)[-1]
                if "." in leaf:
                    names.add(leaf)
    return names


# Level-1 keep decision: obvious scratch skipped by folder + glob (misc/output_durability.md
# §6.1). Kept deliberately small — the agent's level-2 triage (keep_outputs) handles ambiguity.
_TRANSIENT_DIRS = {"tmp", "temp", "cache", ".cache", "__pycache__", ".ipynb_checkpoints", ".git"}
_TRANSIENT_GLOBS = ("*.tmp", "*.pyc", "*.lock", "chunk_*", "*~", ".DS_Store")

# R3: durable-view state → summary-count key. The state vocabulary is weft-truth (§6.2):
# retained/saving are weft's durability; in-store is aba's serving cache only; at-risk is a
# large output live on scratch that nothing has kept yet (RED — the crown-jewel-in-danger).
_COUNT_KEY = {"retained": "retained", "saving": "saving", "in-store": "in_store",
              "at-risk": "at_risk", "in-sandbox": "in_sandbox", "cleared": "cleared",
              "unknown": "unknown"}


def _is_transient(path: str) -> bool:
    """True for obvious scratch — a path UNDER a transient dir, or a transient basename."""
    import fnmatch
    parts = path.split("/")
    if any(seg in _TRANSIENT_DIRS for seg in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in _TRANSIENT_GLOBS)


def _keeper_set(produced: set, include=(), exclude=()) -> set:
    """The set of produced paths to retain, applying the two-level keep decision
    (misc/output_durability.md §6.1): level-1 auto-baseline drops obvious scratch (`_is_transient`);
    level-2 is the agent's `keep_outputs` triage — `exclude` globs drop ambiguous scratch even if it
    looks like a keeper, `include` globs rescue a file the level-1 heuristic would have dropped (and
    add an agent-named literal path that isn't among the produced set, e.g. a declared final)."""
    import fnmatch
    inc = list(include or [])
    exc = list(exclude or [])

    def _m(rel: str, globs) -> bool:
        return any(fnmatch.fnmatch(rel, g) for g in globs)

    keep = set()
    for p in produced:
        if _m(p, exc):                              # agent dropped it → never keep, even a keeper
            continue
        if _is_transient(p) and not _m(p, inc):     # level-1 scratch, unless the agent rescued it
            continue
        keep.add(p)
    for g in inc:                                   # an agent-named literal path not among produced
        if not any(c in g for c in "*?[") and g not in keep and not _m(g, exc):
            keep.add(g)
    return keep


def _retained_so_far(run_id: str) -> tuple:
    """`(decided, placed)` across this Run's existing retain rows: `decided` is the literal
    include paths of every pending/done row's selection — the keeper decisions weft already
    holds; `placed` is the relpaths a DONE retain physically kept (sidecar). Backs the
    CUMULATIVE resubmit in `_retain_run_outputs`: weft's `put_retained` keeps ONE row per
    target (INSERT OR REPLACE), so a delta submit would overwrite earlier turns' pins and
    lose them at settlement."""
    import json as _json
    from core.compute import retention
    decided: set = set()
    placed: set = set()
    try:
        for row in (retention.retained(label=run_id) or []):
            st = row.get("state")
            if st == "done":
                placed |= _sidecar_files(retention.location_path(row))
            if st in ("done", "pinned-pending", "queued", "inflight"):
                try:
                    sel = _json.loads(row.get("selection") or "{}")
                except Exception:  # noqa: BLE001
                    sel = {}
                decided |= {g for g in (sel.get("include") or [])
                            if not any(c in g for c in "*?[")}   # literal include paths only
    except Exception as e:  # noqa: BLE001
        _log.debug("already-retained lookup failed for %s: %s", run_id, e)
    return decided, placed


# Directory-shaped stores (a chunked columnar/array store is a DIRECTORY, not a file).
# Harvest lists single files by extension, so these never reach artifacts_for_run —
# the jobdir scan below is how they enter the keeper set (P1 / #71). Suffix-matched
# with endswith so multi-dot names (`x.lstar.zarr`) qualify too.
_STORE_DIR_SUFFIXES = (".zarr",)
_STORE_SCAN_MAX_DIRS = 2000   # bound the walk on a chunk-heavy jobdir


def _jobdir_store_dirs(run_id: str) -> set:
    """Sandbox-relative paths of store-suffix DIRECTORIES in the Run's local weft
    jobdir(s) — retain candidates that harvest (file-only) can't see. Does not descend
    into a matched store (its chunks travel with the directory literal) or into
    transient dirs; bounded; best-effort empty on any failure."""
    import os as _os
    out: set = set()
    visited = 0
    try:
        for root in _run_jobdirs(run_id):
            rootr = _os.path.realpath(root)
            if not _os.path.isdir(rootr):
                continue
            for dirpath, dirnames, _files in _os.walk(rootr):
                visited += 1
                if visited > _STORE_SCAN_MAX_DIRS:
                    break
                keep_dirs = []
                for d in dirnames:
                    if d in _TRANSIENT_DIRS or d.startswith("."):
                        continue
                    if d.lower().endswith(_STORE_DIR_SUFFIXES):
                        rel = _os.path.relpath(_os.path.join(dirpath, d), rootr)
                        out.add(rel.replace(_os.sep, "/"))
                        continue                 # a store travels as a unit — don't walk chunks
                    keep_dirs.append(d)
                dirnames[:] = keep_dirs
    except Exception as e:  # noqa: BLE001 — a failed scan only means no dir stores this pass
        _log.debug("jobdir store scan failed for %s: %s", run_id, e)
    return out


def _disk_truth_includes(run_id: str, run_metadata: dict, includes,
                         produced: set) -> tuple[set, set, list]:
    """F10 (PK-approved, 2026-07-20): EXPLICIT keep = DISK TRUTH. The automatic
    harvest allowlist (`_FILE_EXTS`) decides what gets TRACKED, never what CAN
    be kept. Literal named includes already enter the keeper set (`_keeper_set`
    adds agent-named literals); this closes the two remaining gaps:
      * GLOB includes resolve against the run's REAL on-disk listing (local
        sandbox walk + each weft target's inventory), so `keep=['out/*.dat']`
        works even though `.dat` is not a harvested extension. Matches that are
        already tracked cost nothing; UNTRACKED matches are added as concrete
        rels — gated by total size (FETCH_GUARDRAIL_BYTES, the same "never a
        silent multi-GB commitment" doctrine as ship-home) so a broad glob
        can't silently pin a huge tree. Gated globs are surfaced, not dropped.
      * LITERAL includes are checked against the same listing, so the keep
        tool's coverage note can tell the truth ("on disk, will be kept")
        instead of pessimistically warning NOT-COVERED for every untracked file.
    Returns (glob_added_rels, literals_seen_on_disk, size_gated_reports).
    Best-effort: listing failures degrade to empty (behavior = pre-F10)."""
    import fnmatch
    import os
    from pathlib import Path
    inc = [s for s in (includes or []) if s and str(s).strip()]
    if not inc:
        return set(), set(), []
    listing: dict[str, int] = {}                       # rel -> bytes
    try:                                               # local sandbox walk
        ap = (get_entity(run_id) or {}).get("artifact_path")
        if ap and os.path.isdir(ap):
            base = Path(ap)
            for f in base.rglob("*"):
                if f.is_file():
                    try:
                        listing[str(f.relative_to(base))] = f.stat().st_size
                    except OSError:
                        pass
    except Exception:  # noqa: BLE001
        pass
    for t in (run_metadata.get("weft_targets") or []):  # remote target inventories
        try:
            from core.compute.adapter import get_compute
            inv = get_compute().sync_call("run_inventory", t)
            for e in (inv.get("entries") or inv.get("files") or []):
                if isinstance(e, dict) and e.get("path"):
                    listing.setdefault(e["path"], int(e.get("bytes") or 0))
        except Exception:  # noqa: BLE001
            continue
    if not listing:
        return set(), set(), []
    from core.data.datasets import FETCH_GUARDRAIL_BYTES
    glob_added: set = set()
    disk_seen: set = set()
    size_gated: list = []
    basenames = {r.rsplit("/", 1)[-1]: r for r in listing}
    for pat in inc:
        if not any(c in pat for c in "*?["):           # literal name
            rel = pat if pat in listing else basenames.get(pat)
            if rel is not None:
                disk_seen.add(pat)
            continue
        matched = {r for r in listing
                   if fnmatch.fnmatch(r, pat)
                   or fnmatch.fnmatch(r.rsplit("/", 1)[-1], pat)}
        untracked = matched - produced
        if not untracked:
            continue
        total = sum(listing[r] for r in untracked)
        if total > FETCH_GUARDRAIL_BYTES:
            size_gated.append({"glob": pat, "files": len(untracked),
                               "bytes": total})
            continue
        glob_added |= untracked
    return glob_added, disk_seen, size_gated


def _retain_run_outputs(run_id: str, run_metadata: dict) -> dict:
    """Durably retain this Run's KEEPER outputs against its weft target(s) — labeled to the
    Run (runs/<run>/<target>/), pinned-pending on the live session kernel (captured at
    settlement; §6.3). CUMULATIVE + IDEMPOTENT: each call submits the FULL keeper set
    decided so far (an idempotent replace — weft keeps one row per target, so a delta
    submit would drop earlier turns' pins at settlement), skipping only when nothing new
    was decided. Safe to call at every turn end, plan-end, Run-close, and each extension.
    The Run's OWN produced sandbox-relative paths (the shared kernel sandbox spans Runs),
    incl. oversize link-only files (§9 A0), directory-shaped stores from the jobdir scan
    (#71 — harvest is file-only), + declared `produces:` (§6 rank-1).
    Best-effort — retention must never break a turn, but failures are surfaced on the Run
    (`metadata.retention_alert`) rather than swallowed."""
    targets = list(run_metadata.get("weft_targets") or [])
    if not targets:
        return {}  # jupyter / no weft kernel → nothing to retain against
    try:
        from core.exec.artifacts import artifacts_for_run
        arts = artifacts_for_run(run_id)
    except Exception as e:  # noqa: BLE001
        _log.warning("retain: could not list artifacts for run %s: %s", run_id, e)
        return {}
    produced = {(a.get("original_name") or "").strip() for a in arts} - {""}
    produced |= _jobdir_store_dirs(run_id)   # directory stores — invisible to harvest
    declared = _declared_output_names(run_metadata)
    if declared:
        produced_basenames = {p.rsplit("/", 1)[-1] for p in produced}
        produced |= {n for n in declared if n not in produced_basenames}
    decision = run_metadata.get("keep_decision") or {}            # level-2: agent keep_outputs triage
    # F10: explicit keep = disk truth (globs resolve against the real listing,
    # size-gated; literals validated for honest coverage reporting).
    glob_added, disk_seen, size_gated = _disk_truth_includes(
        run_id, run_metadata, decision.get("include"), produced)
    produced |= glob_added
    info = {"disk_kept": sorted(glob_added), "disk_seen": sorted(disk_seen),
            "size_gated": size_gated}
    keep = _keeper_set(produced, decision.get("include"), decision.get("exclude"))
    decided, placed = _retained_so_far(run_id)
    if not (keep - decided - placed):
        return info   # nothing newly decided — the stored selection / retained tree covers it
    cumulative = sorted(keep | decided)      # full keeper set, never a delta (§6.3)
    if not cumulative:
        return info   # run_retain errors on an empty match
    # Attribute each keeper to the target that ACTUALLY produced it, so a multi-target
    # Run (a backend restart mid-Run minted a new kernel_id → both recorded) retains
    # each target's OWN files against it. A blanket include per target would settle the
    # OTHER target's files as `retain.pin_missing` (not in that sandbox) — noisy
    # half-failed rows, a lie in the index. Single-target (the norm) sends the full
    # cumulative set to the one target, unchanged.
    from core.compute import retention
    per_target = ({targets[0]: cumulative} if len(targets) == 1
                  else _attribute_keepers(arts, targets, cumulative))
    errors: list = []
    missing: list = []           # (target, include, error) — maybe-benign
    ok_rels: set = set()         # rels a target accepted without raising
    for t in targets:
        include = per_target.get(t) or []
        if not include:
            continue
        try:
            retention.retain(t, include=include, label=run_id,
                             layout="label", background=True)
            ok_rels.update(include)
        except Exception as e:  # noqa: BLE001 — logged + surfaced, never blocks the turn
            from core.compute.errors import ComputeError
            if isinstance(e, ComputeError) and e.code == "retain.no_durable":
                err = _no_durable_keep_policy(t, include, run_id)
                if err:
                    errors.append(err)
                continue
            if isinstance(e, ComputeError) and e.code == "data.missing":
                # a multi-target run's unknown-producer fallback sends a rel
                # to EVERY target; the ones that never held it refuse with
                # data.missing. That is only a problem if NO target holds it
                # — defer the verdict to after the loop.
                missing.append((t, set(include), e))
                continue
            _log.warning("retain failed for run %s target %s: %s", run_id, t, e)
            errors.append(f"{t}: {e}")
    for t, inc, e in missing:
        uncovered = inc - ok_rels
        if uncovered:
            errors.append(f"{t}: {e}")
        else:
            _log.info("retain: %s had none of %s — covered by another "
                      "target, no alert", t, sorted(inc))
    _note_retention_alert(run_id, run_metadata, "; ".join(errors) if errors else None)
    return info


def _attribute_keepers(arts: list, targets: list, cumulative: list) -> dict:
    """Map each keeper relpath to the weft target that produced it (via the artifact's
    exec record's `weft_target`), so a multi-target Run retains each target's OWN files.
    Keepers with no identifiable producer (a declared-but-unsurfaced final, or one carried
    over from a prior turn's `decided` set) go to ALL targets — a redundant pin_missing on
    the wrong target beats losing a crown-jewel file."""
    from core.graph import exec_records
    _cache: dict = {}

    def _target_of(exec_id):
        if exec_id not in _cache:
            try:
                rec = exec_records.get(exec_id) or {}
                # kernel-lane records stamp weft_target; BACKGROUND-job
                # records carry the same identity as compute.job_id only —
                # without this fallback every bg-job keeper attributed to
                # ALL targets, spraying data.missing retains at kernel
                # sandboxes that never held the file (live badges finding)
                _cache[exec_id] = (rec.get("weft_target")
                                   or (rec.get("compute") or {}).get("job_id"))
            except Exception:  # noqa: BLE001
                _cache[exec_id] = None
        return _cache[exec_id]

    file_targets: dict = {}
    for a in arts:
        rel = (a.get("original_name") or "").strip()
        if not rel:
            continue
        tgt = _target_of(a.get("exec_id"))
        if tgt in targets:
            file_targets.setdefault(rel, set()).add(tgt)
    per_target: dict = {t: [] for t in targets}
    for rel in cumulative:
        for t in (file_targets.get(rel) or targets):   # unknown producer → all targets
            per_target[t].append(rel)
    return {t: sorted(v) for t, v in per_target.items()}


def _no_durable_keep_policy(target: str, keepers: list, run_id: str):
    """retention2's refusal, resolved by aba's SIZE-GATED default (never a
    silent multi-GB transfer, never a silent loss): small keeper sets ship
    to the controller workspace with a note; big ones become a Run alert
    carrying weft's own levers (declare durable storage on the machine, or
    ship explicitly). Returns an alert string, or None when handled."""
    from core.compute import retention
    from core.compute.adapter import get_compute
    from core.data.datasets import FETCH_GUARDRAIL_BYTES
    total = None
    try:
        inv = get_compute().sync_call("run_inventory", target)
        names = set(keepers)
        total = sum(e.get("bytes", 0) for e in (inv.get("entries") or inv.get("files") or [])
                    if isinstance(e, dict)
                    and (e.get("path") in names
                         or (e.get("path") or "").rsplit("/", 1)[-1] in names))
    except Exception:  # noqa: BLE001 — unknown size reads as "big": ask, don't ship
        pass
    if total is not None and total <= FETCH_GUARDRAIL_BYTES:
        try:
            retention.retain(target, include=list(keepers), label=run_id,
                             layout="label", background=True,
                             dest="@workspace")
            _log.info("run %s: keepers (%.1f MB) shipped to the workspace — "
                      "site has no durable storage", run_id, total / 1e6)
            return None
        except Exception as e:  # noqa: BLE001
            return f"{target}: ship-home failed: {e}"
    size = f"{total / 1e9:.1f} GB" if total is not None else "an unknown size"
    return (f"results not kept: the machine that ran this has no safe "
            f"storage and the keepers total {size}. Options: declare "
            f"durable storage on its machine card (Settings → Compute), or "
            f"ask to ship them here explicitly.")


def _note_retention_alert(run_id: str, run_metadata: dict, msg) -> None:
    """Persist (or clear, msg=None) a retain-failure alert on the Run, so headroom
    refusals / retain.failed reach the Run card instead of dying in a log line. Mutates
    the caller's metadata dict too — close_run rewrites the entity from its own copy
    right after, and must not clobber the alert. Best-effort."""
    changed = (run_metadata.get("retention_alert") or None) != (msg or None)
    if msg:
        run_metadata["retention_alert"] = msg
    else:
        run_metadata.pop("retention_alert", None)
    if not changed:
        return
    try:
        if not get_entity(run_id):
            return
        from core.graph.entities import patch_metadata
        patch_metadata(run_id, {"retention_alert": msg or None})  # None removes
    except Exception:  # noqa: BLE001 — the alert is best-effort bookkeeping
        pass


def retain_run_keepers(run_id: str) -> None:
    """Retain a Run's keepers NOW — the turn-end reconciliation entry point (guide_hooks:
    on_stop for EVERY completed turn incl. plain re-runs, on_turn_failed for crashed ones,
    on_plan_complete at plan-end), so durability + the Files panel are ready promptly
    instead of waiting for Run-close. Cumulative + idempotent (a call with nothing newly
    decided is a no-op); the close_run call is then a backstop."""
    try:
        ent = get_entity(run_id)
        if ent:
            _retain_run_outputs(run_id, ent.get("metadata") or {})
    except Exception as e:  # noqa: BLE001
        _log.debug("retain_run_keepers failed for %s: %s", run_id, e)


def set_keep_decision(run_id: str, keep=None, drop=None) -> dict:
    """Record + apply the agent's level-2 keep decision (the `keep_outputs` tool;
    misc/output_durability.md §6.1, A1). `keep`/`drop` are sandbox-relative paths or globs:
    `keep` rescues an ambiguous file the folder-level auto-baseline would drop (or names a final
    to retain now); `drop` excludes an ambiguous large intermediate the heuristic would keep. The
    decision persists on the Run (`metadata.keep_decision`) so the plan-end + close auto-retains
    honor it too, and we apply it NOW — retaining the resulting keeper set incrementally. Returns
    the merged decision + the post-apply durable summary so the agent sees the effect.

    Level-1 (obvious scratch by folder/glob) stays automatic; this is only the ambiguous set —
    keep it light, the agent needn't enumerate every file."""
    ent = get_entity(run_id)
    if not ent:
        return {"error": f"run {run_id} not found"}
    md = dict(ent.get("metadata") or {})
    dec = dict(md.get("keep_decision") or {})
    dec["include"] = sorted(set(dec.get("include") or []) | {s.strip() for s in (keep or []) if s and s.strip()})
    dec["exclude"] = sorted(set(dec.get("exclude") or []) | {s.strip() for s in (drop or []) if s and s.strip()})
    md["keep_decision"] = dec
    from core.graph.entities import patch_metadata
    patch_metadata(run_id, {"keep_decision": dec})   # single-key: no blob race
    info = _retain_run_outputs(run_id, md) or {}   # apply now, honoring the merged decision
    try:
        summary = run_durable_view(run_id).get("summary")
    except Exception:  # noqa: BLE001 — the decision is recorded regardless of the view
        summary = None
    out = {"run_id": run_id, "decision": dec, "summary": summary}
    # F10 disk-truth report: which glob-matched untracked rels were kept, which
    # literal includes exist on disk, and any size-gated globs (surfaced, not
    # silently dropped) — the keep tool folds these into its coverage note.
    out.update({k: v for k, v in info.items() if v})
    return out


def bring_back_run(run_id: str, force: bool = False) -> dict:
    """§8e.4 (more_weft_ui.md) bring-back: ship the Run's KEPT files to the
    workspace — a managed local copy. This moves the LOCATION axis only; the
    in-place keeps stay kept where they are (the promise is untouched), weft
    just places a durable copy at @workspace. Selection = the kept rels the
    retained index records for this Run per target, so nothing unkept rides
    along."""
    import json as _json
    ent = get_entity(run_id)
    if not ent:
        return {"error": f"run {run_id} not found"}
    targets = list((ent.get("metadata") or {}).get("weft_targets") or [])
    if not targets:
        return {"error": "run has no compute target to bring files from"}
    from core.compute import retention
    per_target: dict = {t: set() for t in targets}
    for row in (retention.retained(label=run_id) or []):
        t = row.get("target")
        if t not in per_target or row.get("state") not in ("done", "pinned-pending"):
            continue
        rels = _sidecar_files(retention.location_path(row))
        if not rels:
            try:
                rels = set(_json.loads(row.get("selection") or "{}").get("include") or [])
            except Exception:  # noqa: BLE001
                rels = set()
        per_target[t] |= rels
    # SAME size gate as the ship-home policy (one doctrine: never a silent
    # multi-GB transfer) — bring-back bypassed it, so one click could pull a
    # multi-hundred-GB kept store onto the controller (limits-parity review).
    # `force=True` is the explicit "ship it anyway" lever.
    from core.compute.adapter import get_compute
    from core.data.datasets import FETCH_GUARDRAIL_BYTES
    if not force:
        total = 0
        sized = True
        for t, rels in per_target.items():
            if not rels:
                continue
            try:
                inv = get_compute().sync_call("run_inventory", t)
                names = set(rels)
                total += sum(e.get("bytes", 0)
                             for e in (inv.get("entries") or inv.get("files") or [])
                             if isinstance(e, dict)
                             and (e.get("path") in names
                                  or (e.get("path") or "").rsplit("/", 1)[-1] in names))
            except Exception:  # noqa: BLE001 — unknown size reads as "big"
                sized = False
        if not sized or total > FETCH_GUARDRAIL_BYTES:
            size = f"{total / 1e9:.1f} GB" if sized else "an unknown total size"
            return {"error": f"bring-back is {size} — larger than the "
                             f"{FETCH_GUARDRAIL_BYTES / 1e9:.0f} GB guardrail. "
                             f"Bring files back selectively, or pass force=true "
                             f"to ship everything anyway."}
    requested = 0
    errors: list = []
    for t, rels in per_target.items():
        if not rels:
            continue
        try:
            retention.retain(t, include=sorted(rels), dest="@workspace",
                             label=run_id, background=True)
            requested += len(rels)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
    if not requested and not errors:
        return {"error": "nothing kept for this run to bring back"}
    return {"ok": not errors, "requested": requested, "errors": errors or None}


def _sidecar_files(location: Optional[str]) -> set:
    """The relpaths a DONE retain kept — from its `.weft-run.json` sidecar (§6.1b),
    falling back to walking the retained dir. Empty for a location we can't read
    (e.g. a remote in-place site — caller then leans on the row/inventory)."""
    import os as _os
    import json as _json
    if not location or not _os.path.isdir(location):
        return set()
    sc = _os.path.join(location, ".weft-run.json")
    if _os.path.isfile(sc):
        try:
            with open(sc) as fh:
                data = _json.load(fh)
            fs = {f.get("path") for f in (data.get("files") or []) if f.get("path")}
            if fs:
                return fs
        except Exception:  # noqa: BLE001
            pass
    out = set()
    for base, _dirs, files in _os.walk(location):
        for f in files:
            if f == ".weft-run.json":
                continue
            out.add(_os.path.relpath(_os.path.join(base, f), location))
    return out


def _kernel_site_map() -> dict:
    """{target: site} for the compute targets weft currently knows — so a Run's
    output can be attributed to the machine it lives on. Best-effort; a stopped
    kernel may be absent (the retained index then supplies the site)."""
    try:
        from core.compute import get_compute
        return {k.get("kernel_id"): k.get("site")
                for k in (get_compute().sync_call("list_kernels").get("kernels") or [])
                if k.get("kernel_id")}
    except Exception:  # noqa: BLE001 — no map just means we fall back to the index
        return {}


def _is_kernel_target(t) -> bool:
    return isinstance(t, str) and t.startswith("krn_")


def _safe_join(base: Optional[str], rel: str) -> Optional[str]:
    """Join a caller-controlled `rel` under `base`, refusing escapes: an absolute
    `rel` or any `..` segment is rejected, and the normalized candidate must stay
    under `base`. Returns the joined path, or None on rejection — NEVER raises into
    a route. Pure lexical (normpath), so it is valid for a remote abs path (a
    kernel sandbox) as well as a local cache dir. Callers must treat None as
    'refuse' (no cache read, no cache write, no fetch)."""
    import os as _os
    if not base or not rel:
        return None
    r = str(rel).replace("\\", "/")
    if r.startswith("/") or _os.path.isabs(r):
        return None
    if any(seg == ".." for seg in r.split("/")):
        return None
    baser = _os.path.normpath(base)
    cand = _os.path.normpath(_os.path.join(baser, r))
    if cand != baser and not cand.startswith(baser + _os.sep):
        return None
    return cand


@lru_cache(maxsize=32)
def _site_root(site: str) -> Optional[str]:
    """A registered site's weft root abs path (from its capabilities/config) —
    the base under which a kernel sandbox lives (`<root>/kernels/<kernel_id>`).
    None when unknown. Cached (site config doesn't change mid-session)."""
    try:
        from core.compute.adapter import get_compute
        d = get_compute().sync_call("sites_describe", site) or {}
        return (d.get("config") or {}).get("root")
    except Exception:  # noqa: BLE001
        return None


def _kernel_abs_path(target: str, site: str, rel: str) -> Optional[str]:
    """Absolute path of a rel inside a LIVE kernel's sandbox on its site
    (`<root>/kernels/<kernel_id>/<rel>`) — the handle the data-plane
    (`register_source → fetch`) needs to bring a store/large file home from an
    OPEN run (the retain lane defers on a live kernel; the read lane caps at
    8 MB). None when the site root is unknown, the target isn't a kernel, or
    `rel` escapes the kernel sandbox (absolute / `..` — the normpath must stay
    under `<root>/kernels/<target>/`)."""
    if not _is_kernel_target(target):
        return None
    root = _site_root(site)
    if not root:
        return None
    return _safe_join(f"{root.rstrip('/')}/kernels/{target}", rel)


def _data_plane_fetch(abs_path: str, site: str, dest: str,
                      *, force: bool = False) -> bool:
    """Bring a remote abs path (file or directory) home to `dest` over the
    datasets data-plane (`register_source → fetch`) — one transfer, all sizes,
    size-gated by the same `FETCH_GUARDRAIL_BYTES` fetch enforces (`force=True`
    bypasses that gate for an explicit forced bring-back). True on a complete
    local copy. The transport datasets already use; not a new one."""
    try:
        import os as _os
        from core.data import datasets as _ds
        _os.makedirs(_os.path.dirname(dest) or ".", exist_ok=True)
        meta = _ds.register_source(abs_path, site=site)
        res = _ds.fetch(meta, dest, force=force)
        return bool(res.get("ok")) and _os.path.exists(dest)
    except Exception as e:  # noqa: BLE001
        _log.debug("_data_plane_fetch(%s@%s) failed: %s", abs_path, site, e)
        return False


def _live_inventory(target: str, **kw) -> dict:
    """Inventory a target, preferring the LIVE sandbox for a kernel target — an
    OPEN run's outputs sit in the live kernel dir, which has NO terminal record
    yet (`run_inventory` without live raises data.missing). Try live-first for a
    kernel, terminal-first otherwise, falling back across both so a just-settled
    kernel still resolves. {} on total failure (caller reads as 'unknown → too big')."""
    from core.compute.adapter import get_compute
    c = get_compute()
    for live in ((True, False) if _is_kernel_target(target) else (False, True)):
        try:
            return c.sync_call("run_inventory", target, live=live, **kw)
        except Exception:  # noqa: BLE001
            continue
    return {}


def _live_file_stat(target: str, rel: str) -> dict:
    """file_stat, live-aware for a kernel target (open run). For a kernel, try a
    LIVE-flag read first (its dir has no terminal record); otherwise use the
    `retention.file_stat` wrapper. Both fall back to the other. {} on failure."""
    from core.compute import retention

    def _live():
        try:
            from core.compute.adapter import get_compute
            st = get_compute().sync_call("run_file_stat", target, rel, live=True)
            return st if isinstance(st, dict) else None
        except Exception:  # noqa: BLE001
            return None

    def _wrapped():
        try:
            return retention.file_stat(target, rel)
        except Exception:  # noqa: BLE001
            return None

    for fn in ((_live, _wrapped) if _is_kernel_target(target) else (_wrapped, _live)):
        st = fn()
        if st is not None:
            return st
    return {}


def _run_remote_targets(run_id: str) -> list:
    """`[(target, site)]` — the Run's targets whose bytes live on a NON-local site,
    from retained rows (which carry the site) + the Run's recorded weft targets.
    Used by both the single-FILE locate and the DIRECTORY-store bring-back."""
    from core.compute import retention
    md = (get_entity(run_id) or {}).get("metadata") or {}
    kmap = _kernel_site_map()
    site_of: dict = {}
    try:
        for row in (retention.retained(label=run_id) or []):
            t, s = row.get("target"), row.get("site")
            if t and s:
                site_of.setdefault(t, s)
    except Exception as e:  # noqa: BLE001
        _log.debug("_run_remote_targets: retained() failed for %s: %s", run_id, e)
    out = []
    for t in dict.fromkeys(list(site_of) + list(md.get("weft_targets") or [])):
        site = site_of.get(t) or kmap.get(t) or "local"
        if site != "local":
            out.append((t, site))
    return out


def run_output_site(run_id: str, rel: str) -> Optional[str]:
    """The site where a Run output's bytes live, when that is NOT this controller —
    for honest "on <site>" messaging in the serving routes. None when the file is
    local, unknown, or the substrate is unreachable. Best-effort, never raises."""
    try:
        loc = locate_run_output(run_id, rel)
    except Exception:  # noqa: BLE001
        return None
    return loc["site"] if loc and loc.get("locality") == "remote" else None


def _fetched_cache_dir(run_id: str):
    """Stable local cache dir for bytes fetched from a remote site to open a Run
    output — a `<run_id>-fetched` sibling under the project scratch tree, so a
    second open is a free cache hit (durability + resolvability converge: fetching
    to view IS bringing a local copy home)."""
    from core.data.workspace import scratch_dir
    from core import projects
    pid = str(projects.current() or "default")
    return scratch_dir(pid, f"{run_id}-fetched")


def resolve_run_file(run_id: str, rel: str) -> Optional[str]:
    """Absolute on-disk path for one of a Run's FILES at its EXACT rel — the
    serve/archive policy over the canonical pair: `locate_run_output`
    (match="exact" — a same-named file elsewhere must never answer a /file
    request) + a transparent `materialize_run_output` under the request-blocking
    gate (`_MAX_HARVEST_BYTES` — these surfaces block an HTTP request on the
    fetch, so their budget is small). Files above the gate — and of UNKNOWN
    size — stay remote (None → the route names the site honestly). Never
    raises."""
    from core.exec.run import _MAX_HARVEST_BYTES
    try:
        loc = locate_run_output(run_id, rel, match="exact")
        if not loc or loc.get("kind") != "file":
            return None
        if loc.get("local_path"):
            return loc["local_path"]
        return materialize_run_output(loc, max_bytes=_MAX_HARVEST_BYTES)
    except Exception as e:  # noqa: BLE001 — a resolver must degrade, not raise
        _log.debug("resolve_run_file failed for %s/%s: %s", run_id, rel, e)
        return None


@lru_cache(maxsize=512)
def _glob_to_re(pat: str):
    """A path-aware glob → compiled regex: `*` matches WITHIN a path segment
    (does not cross `/`), `**` crosses segments, `?` is one non-slash char.
    Plain `fnmatch` makes `*` span `/`, so `*.txt` wrongly matches `sub/a.txt`
    and over-claims durability for nested files — this doesn't."""
    i, n, out = 0, len(pat), []
    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                out.append(".*"); i += 2; continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$")


def _glob_match(rel: str, pattern: str) -> bool:
    return bool(_glob_to_re(pattern).match(rel))


_PREVIEW_CAP = 8 * 1024 * 1024   # weft run_file_read hard cap; a preview channel, not transport


def read_run_file(run_id: str, rel: str, max_bytes: int = _PREVIEW_CAP):
    """Preview bytes of an IN-SANDBOX file via weft `run_file_read` (base64), across the
    Run's targets — for a file that isn't in the local retained tree yet (B1b). Returns
    `(bytes, truncated, total)`, or `(None, False, 0)` if unreadable. `truncated=True` means
    the file is bigger than the preview channel — Keep it (→ retain) then download; big
    bytes travel via `data_register`→`data_fetch`, not here. weft confines the path to the
    jobdir + refuses traversal."""
    import base64
    from core.compute import retention
    ent = get_entity(run_id)
    for t in ((ent or {}).get("metadata") or {}).get("weft_targets") or []:
        try:
            rd = retention.file_read(t, rel, max_bytes=max_bytes)
        except Exception:  # noqa: BLE001 — swept / gone on this target → try the next
            continue
        b64 = rd.get("bytes_b64")
        if b64 is not None:
            return (base64.b64decode(b64), bool(rd.get("truncated")),
                    int(rd.get("bytes_total") or 0))
    return (None, False, 0)


def _search_root_for(root: Optional[str], name: str) -> Optional[str]:
    """Absolute path of a FILE or DIRECTORY under `root` matching `name` (an exact rel join,
    else a basename match anywhere below). Escape-safe. Prefers a directory (a `.zarr`/store
    dir) over a same-named file, then newest mtime, and does NOT descend into a matched dir
    (bounds cost for chunk-heavy stores). None if not found."""
    import os as _os
    if not root:
        return None
    rootr = _os.path.realpath(root)
    if not _os.path.isdir(rootr):
        return None
    cand = _os.path.realpath(_os.path.join(rootr, name))          # exact rel join first (cheap)
    if (cand == rootr or cand.startswith(rootr + _os.sep)) and _os.path.exists(cand):
        return cand
    base = name.rsplit("/", 1)[-1]
    hits: list = []
    for dirpath, dirnames, filenames in _os.walk(rootr):
        for d in [d for d in dirnames if d == base]:              # matched dir (e.g. x.lstar.zarr)
            hits.append(_os.path.join(dirpath, d))
        dirnames[:] = [d for d in dirnames if d != base]          # prune → don't walk store chunks
        for f in filenames:
            if f == base:
                hits.append(_os.path.join(dirpath, f))
        if len(hits) > 50:                                        # bound the scan
            break
    if not hits:
        return None

    def _key(p: str):
        try:
            return (1 if _os.path.isdir(p) else 0, _os.path.getmtime(p))
        except OSError:
            return (0, 0.0)
    return max(hits, key=_key)


def _run_jobdirs(run_id: str) -> list:
    """Absolute LOCAL weft jobdirs for a Run's targets. A weft kernel can't chdir — its cwd IS
    its jobdir (`weft_workspace/site-local/<jobdir>`), so bare relative writes (a produced
    `.zarr` store, an .h5ad) land there, and aba harvests from there. This is where a
    freshly-produced, not-yet-retained output physically lives for the local site."""
    out: list = []
    ent = get_entity(run_id)
    targets = ((ent or {}).get("metadata") or {}).get("weft_targets") or []
    if not targets:
        return out
    try:
        from core.compute import adapter as _ad, get_compute
        ws = _ad.weft_workspace()
        kmap = {k.get("kernel_id"): k
                for k in (get_compute().sync_call("list_kernels").get("kernels") or [])}
        for t in targets:
            k = kmap.get(t)
            if k and k.get("site") == "local" and k.get("jobdir"):
                out.append(str(ws / "site-local" / k["jobdir"]))
    except Exception as e:  # noqa: BLE001 — no jobdir just means we fall back to other tiers
        _log.debug("run jobdir lookup failed for %s: %s", run_id, e)
    return out


def _sidecar_resolve(loc: str, name: str) -> Optional[str]:
    """Resolve `name` inside a RETAINED location via its `.weft-run.json` sidecar —
    catalog-first, no directory walk (misc/output_serving_model.md: catalog rows are
    per-run, so an artifact/store resolves as location + '/' + rel with the sidecar's
    file list prefix-grouped). Matches an exact rel, a DIRECTORY prefix (a store's
    chunk files enumerate under '<…>/<name>/…' — the match is the store dir itself),
    or a basename. Only sidecar-listed rels are ever joined (never raw caller input),
    so the result can't escape `loc`. None if the sidecar can't resolve it."""
    import os as _os
    files = _sidecar_files(loc)
    if not files:
        return None
    base = name.rsplit("/", 1)[-1]

    def _existing(rel: str) -> Optional[str]:
        p = _os.path.join(loc, rel)
        return _os.path.realpath(p) if _os.path.exists(p) else None
    if name in files:                                 # exact rel
        return _existing(name)
    for rel in sorted(files):                         # store-dir prefix group
        parts = rel.split("/")
        if base in parts[:-1]:
            return _existing("/".join(parts[:parts.index(base) + 1]))
    for rel in sorted(files):                         # basename file match
        if rel.rsplit("/", 1)[-1] == base:
            return _existing(rel)
    return None


def locate_run_output(run_id: str, name: str, *, match: str = "name",
                      remote: bool = True) -> Optional[dict]:
    """THE canonical Run-output resolver: answers WHERE an output lives and how
    big it is, and NEVER moves bytes — a lookup must not transfer; only
    `materialize_run_output` (the one mover) may, on an action surface's
    explicit budget. Every consumption surface (serve, list, view, render,
    download) resolves through here, so the local-or-remote decision has
    exactly one home.

        {run_id, rel, kind: "file"|"dir", locality: "local"|"remote",
         local_path, root, durability, site, target, size, mtime, digest}

    LOCAL tiers first (bytes on this machine → `local_path` set, site "local"),
    catalog-first: weft retained tree (`done` rows; durability="retained") →
    the live weft jobdir(s) ("live"; `rel` is sandbox-relative, retain-include
    ready) → the run sandbox `artifact_path` ("scratch") → weft's own
    (run, rel) key (`run_file_stat.abs_path` — catches keeps under another
    label / moved by PLACE; exact-rel, weft confines traversal). Then the
    REMOTE tier: the Run's non-local targets, confirmed by live-aware
    `file_stat` (a file) or inventory membership (a directory store) —
    `local_path` None, `size` None when unknown/truncated (readers treat that
    as too-big-to-move), `digest` the store's freshness identity (hashed
    member path/bytes/mtime — the same idiom as the data-plane fingerprint).

    `match="name"` resolves a logical name (exact rel, then store-dir prefix,
    then basename — the viewer/lookup semantic); `match="exact"` joins the
    exact rel only (the serve/archive/keep semantic — a same-named file
    elsewhere must NOT answer). `remote=False` skips the remote tier (for a
    bounded scan's cheap local pass). Escape-safe on every tier; never
    raises."""
    import os as _os
    from core.compute import retention
    ent = get_entity(run_id)
    md = (ent or {}).get("metadata") or {}
    tiers: list = []                                   # (root, durability, catalog_first)
    try:
        for row in (retention.retained(label=run_id) or []):
            if row.get("state") == "done":
                loc = retention.location_path(row)
                if loc:
                    tiers.append((loc, "retained", True))
    except Exception as e:  # noqa: BLE001
        _log.debug("locate_run_output: retained() failed for %s: %s", run_id, e)
    tiers.extend((d, "live", False) for d in _run_jobdirs(run_id))
    ap = (ent or {}).get("artifact_path")
    if ap:
        tiers.append((ap, "scratch", False))           # legacy / non-weft fallback
    # exec-cwd tier: every exec attributed to this Run wrote its sidecar under
    # <cwd>/.exec, so dirname² of record_path recovers the directory the bytes
    # were written in — a DETACHED JOB's scratch dir or a kernel sandbox. The
    # kernel-jobdir map above only sees KERNEL targets (list_kernels), so a
    # detached job's outputs were unreachable (found live: dead_link on a file
    # sitting in its job scratch dir).
    try:
        from core.graph import exec_records as _xr
        seen = {_os.path.realpath(r) for r, _, _ in tiers}
        for _ix in _xr.list_by_run(run_id):
            rp = _ix.get("record_path")
            if not rp:
                continue
            cdir = _os.path.dirname(_os.path.dirname(str(rp)))
            if cdir and _os.path.isdir(cdir) \
                    and _os.path.realpath(cdir) not in seen:
                seen.add(_os.path.realpath(cdir))
                tiers.append((cdir, "live", False))
    except Exception as e:  # noqa: BLE001 — a tier must never break the resolver
        _log.debug("locate_run_output: exec-cwd tier failed for %s: %s", run_id, e)

    def _exact_under(root: str) -> Optional[str]:
        baser = _os.path.realpath(root)
        cand = _os.path.realpath(_os.path.join(baser, name))
        if (cand == baser or cand.startswith(baser + _os.sep)) and _os.path.exists(cand):
            return cand
        return None

    for root, durability, catalog_first in tiers:
        if match == "exact":
            hit = _exact_under(root)
        else:
            hit = (_sidecar_resolve(root, name) if catalog_first else None) \
                or _search_root_for(root, name)
        if hit:
            rootr = _os.path.realpath(root)
            isdir = _os.path.isdir(hit)
            size = None
            if not isdir:
                try:
                    size = _os.path.getsize(hit)
                except OSError:
                    size = None
            return {"run_id": run_id,
                    "rel": _os.path.relpath(hit, rootr).replace(_os.sep, "/"),
                    "root": rootr, "local_path": hit,
                    "locality": "local", "site": "local",
                    "durability": durability,
                    "kind": "dir" if isdir else "file",
                    "size": size, "mtime": None, "target": None, "digest": None}
    # retention2 fallback: the (run, relpath) KEY, resolved by weft itself
    # (run_file_stat answers sandbox-or-keep with `at` + a LOCAL abs_path).
    try:
        from core.compute.adapter import get_compute
        comp = get_compute()
        for t2 in md.get("weft_targets") or []:
            st = comp.sync_call("run_file_stat", t2, name)
            pth = st.get("abs_path")
            if st.get("exists") and pth and _os.path.exists(pth):
                real = _os.path.realpath(pth)
                return {"run_id": run_id, "rel": name,
                        "root": _os.path.dirname(real), "local_path": real,
                        "locality": "local", "site": "local",
                        "durability": ("retained" if st.get("at") == "retained"
                                       else "live"),
                        "kind": "dir" if _os.path.isdir(real) else "file",
                        "size": st.get("bytes"), "mtime": st.get("mtime"),
                        "target": t2, "digest": None}
    except Exception as e:  # noqa: BLE001 — a fallback must stay a fallback
        _log.debug("locate_run_output (run,rel) fallback failed for %s: %s",
                   run_id, e)
    # HARVESTED-ARTIFACT tier (serving cache, last local tier): what the run
    # itself ADVERTISES — produced[] entries on its exec records, whose bytes
    # are content-addressed copies under the project's artifacts dir. The
    # name-lookup surfaces (viewer/path-lookup) must see everything the
    # run-file surface serves; resolving them from different catalogs is the
    # presentation-parity violation the surfaces oracle caught live
    # (viewer_blind on a durably-served table).
    try:
        from core.exec.artifacts import artifacts_for_run
        from core.config import project_artifacts_dir
        base = _os.path.basename(name)
        for a in artifacts_for_run(run_id):
            on = (a.get("original_name") or "").strip()
            url = a.get("url") or ""
            if not on or not url.startswith("/artifacts/"):
                continue
            if not ((on == name) if match == "exact"
                    else (on == name or _os.path.basename(on) == base)):
                continue
            parts = url.split("/")            # ['', 'artifacts', pid, served]
            if len(parts) != 4:
                continue
            f = project_artifacts_dir(parts[2]) / parts[3]
            if f.is_file():
                try:
                    size = a.get("size") or _os.path.getsize(str(f))
                except OSError:
                    size = a.get("size")
                # the harvested copy KNOWS its producer: the artifact row
                # carries exec_id, and the exec record's compute block holds
                # the kernel target — surface it, or every consumer that
                # needs the durable (target, rel) identity (run_key capture,
                # retention) sees an identity-less hit whenever this tier
                # answers first (live: the remote wing's last red — a
                # remote-born file's serving copy shadowed the remote tier)
                _tgt = None
                try:
                    from core.graph import exec_records as _er
                    _tgt = (((_er.get(a.get("exec_id")) or {})
                             .get("compute") or {}).get("kernel_id"))
                except Exception:  # noqa: BLE001 — identity is enrichment
                    pass
                return {"run_id": run_id, "rel": on, "root": str(f.parent),
                        "local_path": str(f), "locality": "local",
                        "site": "local", "durability": "store",
                        "kind": "file", "size": size, "mtime": None,
                        "target": _tgt, "digest": a.get("sha256")}
    except Exception as e:  # noqa: BLE001 — a tier must never break the resolver
        _log.debug("locate_run_output: artifact-store tier failed for %s: %s",
                   run_id, e)
    if not remote:
        return None
    # REMOTE tier — the bytes exist, on a non-local site. Locate-only:
    # existence + size + freshness identity; never a transfer.
    try:
        for target, site in _run_remote_targets(run_id):
            st = _live_file_stat(target, name)
            if st.get("exists"):
                return {"run_id": run_id, "rel": name,
                        "root": None, "local_path": None,
                        "locality": "remote", "site": site,
                        "durability": ("retained" if st.get("at") == "retained"
                                       else "live" if _is_kernel_target(target)
                                       else "scratch"),
                        "kind": "file", "size": st.get("bytes"),
                        "mtime": st.get("mtime"), "target": target,
                        "digest": _file_digest(st.get("bytes"), st.get("mtime"))}
            mem = _store_members(target, name)
            if mem:
                return {"run_id": run_id, "rel": name,
                        "root": None, "local_path": None,
                        "locality": "remote", "site": site,
                        "durability": ("live" if _is_kernel_target(target)
                                       else "scratch"),
                        "kind": "dir",
                        "size": (None if mem["truncated"]
                                 else mem["total_bytes"]),
                        "mtime": None, "target": target,
                        "digest": mem["digest"]}
    except Exception as e:  # noqa: BLE001 — the remote tier degrades to None
        _log.debug("locate_run_output remote tier failed for %s/%s: %s",
                   run_id, name, e)
    return None


def resolve_output(run_id: str, name: str) -> Optional[dict]:
    """P3 serving facade — an alias of the canonical `locate_run_output`
    (match="name"). NOTE the honest-locality contract: a REMOTE-only output now
    returns locality="remote" with local_path=None (it used to be None
    outright); path consumers use `resolve_run_output_path` (still
    local-or-None) or materialize explicitly."""
    return locate_run_output(run_id, name)


def resolve_run_output_path(run_id: str, name: str) -> Optional[str]:
    """Absolute LOCAL path to a Run output FILE or DIRECTORY matching `name` (basename or rel),
    across the canonical local tiers (retained tree → live jobdir(s) → sandbox → weft's
    (run,rel) key). Directory-aware and escape-safe. None when not locally resolvable
    (in-sandbox on a REMOTE site, or swept) — this wrapper never moves bytes."""
    info = locate_run_output(run_id, name)
    return info.get("local_path") if info else None


def resolve_entity_output(entity_id: str) -> Optional[dict]:
    """P5 legacy shim: resolve a PINNED entity's bytes through the P3 facade —
    `resolve_output`'s dict, or None.

    An entity materialized from an artifact records its `artifact_path` as an aba
    SERVED path (`/artifacts/<pid>/<hash>.png`) — that copy is aba's own serving
    cache (harvest, size-capped), NOT weft-managed and not covered by `run_forget`.
    So when it's absent (evicted, a project moved, or a link-only oversize artifact
    that was never copied), the entity 404s even though weft holds the bytes
    durably. But the entity already carries a durable REFERENCE it simply never
    used: `exec_id` → the exec record's `run_id`, plus `metadata.original_name`
    (the sandbox-relative path). Resolve through that.

    This is §7's identity migration as a read-side shim: no schema change, legacy
    `/artifacts` paths keep working (callers try them first), and the durable tier
    becomes reachable when they don't. Best-effort — never raises."""
    try:
        ent = get_entity(entity_id)
        if not ent:
            return None
        rel = (ent.get("metadata") or {}).get("original_name")
        exec_id = ent.get("exec_id")
        if not rel or not exec_id:
            return None
        from core.graph import exec_records
        rec = exec_records.get(exec_id) or {}
        rid = rec.get("run_id")
        return resolve_output(rid, rel) if rid else None
    except Exception as e:  # noqa: BLE001 — a shim must never break a download
        _log.debug("resolve_entity_output failed for %s: %s", entity_id, e)
        return None


def _locate_project_run_output(name: str, *, max_runs: int = 12) -> Optional[tuple]:
    """Locate a project Run output matching `name` WITHOUT moving bytes:
    `(run_id, site, size, is_remote)` for a confident match, or None. A cheap
    LOCAL-only locate pass over recent Runs first; then the remote tier over a
    bounded few weft-target candidates. When only remote candidates match and
    MORE THAN ONE run has the output (a bare-basename collision across runs),
    the result is ambiguous → None — run A's output never silently answers a
    request that could be run B's."""
    scanned = 0
    remote_cands: list = []
    for e in reversed(list_entities(type_filter="analysis", include_archived=False)):
        rid = e["id"]
        loc = locate_run_output(rid, name, remote=False)
        if loc and loc.get("local_path"):
            return (rid, "local", loc.get("size"), False)
        if (e.get("metadata") or {}).get("weft_targets"):
            remote_cands.append(rid)
        scanned += 1
        if scanned >= max_runs:
            break
    matches: list = []
    for rid in remote_cands[:4]:
        loc = locate_run_output(rid, name)
        if loc and loc.get("locality") == "remote":
            matches.append((rid, loc))
    if len(matches) == 1:                     # unambiguous remote hit
        rid, loc = matches[0]
        return (rid, loc["site"], loc.get("size"), True)
    return None                               # no match, or ambiguous across runs


def resolve_project_run_output(name: str, *, max_runs: int = 12) -> Optional[tuple]:
    """`(run_id, abs_path)` for a project Run output matching `name` — a LOOKUP
    that NEVER moves bytes (it backs the viewer menu GET `/api/viewers/for`,
    download, and open_viewer). A local output returns its real on-disk path. A
    REMOTE output returns `(run_id, name)` — a REMOTE MARKER where `abs_path`
    is the logical name, NOT an on-disk file — so the viewer LAUNCH path
    (pagoda3._resolve_source → `resolve_run_store`) performs the size-gated,
    freshness-revalidated fetch. (Deliberately no cached-copy fast path here: a
    marker forces the launch to revalidate, so a stale cache of a still-growing
    output can't be handed out as the real thing.) None when no Run confidently
    has the output."""
    loc = _locate_project_run_output(name, max_runs=max_runs)
    if not loc:
        return None
    run_id, _site, _size, is_remote = loc
    if not is_remote:
        hit = resolve_run_output_path(run_id, name)
        return (run_id, hit) if hit else None
    return (run_id, name)                      # remote marker — launch fetches, not the lookup


def run_id_for_entity(entity_id: str) -> Optional[str]:
    """The Run that produced a pinned entity, via its `exec_id` → exec record
    `run_id`. Lets a viewer node carrying only an `entity_id` reach the canonical
    Run resolver. None when the entity isn't run-linked. Best-effort."""
    try:
        ent = get_entity(entity_id)
        if not ent:
            return None
        exec_id = ent.get("exec_id")
        if not exec_id:
            return None
        from core.graph import exec_records
        return (exec_records.get(exec_id) or {}).get("run_id")
    except Exception:  # noqa: BLE001
        return None


def _rel_under_store(path: Optional[str], name: str) -> bool:
    """Is inventory `path` part of the store addressed by `name` (an exact match,
    or a member under the store's dir prefix — matched on the full rel or the
    basename, since a jobdir listing and a logical name may differ in leading
    segments)?"""
    if not path:
        return False
    base = name.rsplit("/", 1)[-1].rstrip("/")
    n = name.rstrip("/")
    return (path == n or path.startswith(n + "/")
            or path == base or path.startswith(base + "/"))


def _store_members(target: str, name: str) -> Optional[dict]:
    """The store addressed by `name` in a target's inventory (live-aware):
    `{n_files, total_bytes, truncated, digest}`, or None when it has no members
    there. `digest` hashes the sorted member (path, bytes, mtime) lines — the
    store's freshness identity AS LISTED NOW (the same idiom as the data-plane
    fingerprint: any write, including a same-size rewrite, changes it; a
    FINISHED target's inventory is frozen, so its digest never changes). None
    digest when the inventory was truncated — an undercount can't identify
    anything, so readers treat the copy as unverifiable. Never raises."""
    try:
        inv = _live_inventory(target)
    except Exception:  # noqa: BLE001
        return None
    entries = [e for e in (inv.get("entries") or inv.get("files") or [])
               if isinstance(e, dict) and _rel_under_store(e.get("path"), name)]
    if not entries:
        return None
    truncated = bool(inv.get("truncated"))
    digest = None
    if not truncated:
        import hashlib as _hl
        lines = sorted(f"{e.get('path')}\t{e.get('bytes', 0)}\t{e.get('mtime', 0)}"
                       for e in entries)
        digest = _hl.sha1("\n".join(lines).encode()).hexdigest()
    return {"n_files": len(entries),
            "total_bytes": sum(e.get("bytes", 0) for e in entries),
            "truncated": truncated, "digest": digest}


def _file_digest(size, mtime) -> str:
    """Freshness digest for a single remote file — its live (bytes, mtime)
    pair. Same role as the store digest: equality ⇒ a fetched copy is
    current; any rewrite (even same-size) changes it."""
    return f"{size or 0}:{mtime or 0}"


def _stamp_read(dest: str) -> Optional[str]:
    """The freshness digest a fetched-home copy at `dest` was stamped with, or
    None (no stamp / unreadable → the copy counts as stale)."""
    import json
    try:
        with open(dest + ".stamp") as fh:
            d = json.load(fh)
        return d.get("digest") if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


# Per-dest install serialization. A directory-store install is check-then-swap
# (is dest present & fresh? → keep; else supersede + rename in) across TWO
# non-atomic files (the dir and its sibling `.stamp`). Two concurrent opens of
# the SAME store can otherwise interleave so the second sees the first's dir
# present but its stamp not yet written, misreads it as stale, and rmtree's the
# fresh copy out from under the first caller (which already handed the path to a
# viewer). A per-dest lock makes the short critical section atomic within the
# server process (the case that actually races: two viewers opening one store).
import threading as _threading
_INSTALL_LOCKS: dict = {}
_INSTALL_LOCKS_GUARD = _threading.Lock()


def _install_lock(dest: str) -> "_threading.Lock":
    with _INSTALL_LOCKS_GUARD:
        lk = _INSTALL_LOCKS.get(dest)
        if lk is None:
            lk = _INSTALL_LOCKS[dest] = _threading.Lock()
        return lk


def _stamp_write(dest: str, digest: Optional[str]) -> None:
    """Record the SOURCE freshness digest the copy at `dest` was fetched
    against — captured at locate time, BEFORE the fetch, so a store that grew
    mid-transfer can never validate as current. No digest (unknown/truncated
    source) → no stamp → the next open re-fetches. Best-effort."""
    if not digest:
        return
    import json
    try:
        with open(dest + ".stamp", "w") as fh:
            json.dump({"digest": digest}, fh)
    except Exception:  # noqa: BLE001
        pass


def materialize_run_output(loc: Optional[dict], *, max_bytes: int,
                           force: bool = False, progress=None) -> Optional[str]:
    """THE single byte-mover for Run outputs. Takes a `locate_run_output`
    result and returns a LOCAL path — as-is for a local hit, otherwise by
    bringing the bytes home. Movement is always deliberate: the CALLER (an
    action surface — a serve route, a viewer launch, a download) chooses
    `max_bytes` as its own budget; an unknown size (None — including a
    truncated store inventory) or an over-budget output is refused (None)
    unless `force` (the user's explicit override). `progress` (optional
    callable) receives human-readable phase strings for the action's UI.

    Caching: fetched copies land in the run's `<run_id>-fetched` scratch cache,
    installed ATOMICALLY and stamped with the source's freshness digest; every
    later open revalidates against the CURRENT digest. A finished target's
    digest never changes → cache hits forever; an OPEN run's changes on any
    write → re-fetch — a frozen first fetch can never masquerade as current.
    Never raises."""
    if not loc:
        return None
    if loc.get("local_path"):
        return loc["local_path"]
    if loc.get("locality") != "remote":
        return None
    try:
        size = loc.get("size")
        if not force and (size is None or size > max_bytes):
            return None                       # unknown or over-budget ⇒ honest refusal
        if loc.get("kind") == "dir":
            return _materialize_store(loc, force=force, progress=progress)
        return _materialize_file(loc, force=force, progress=progress)
    except Exception as e:  # noqa: BLE001 — a mover must degrade, not raise
        _log.debug("materialize_run_output failed for %s/%s: %s",
                   loc.get("run_id"), loc.get("rel"), e)
        return None


def _materialize_file(loc: dict, *, force: bool = False, progress=None) -> Optional[str]:
    """Bring one remote Run FILE home (the caller's gate already passed).
    Lanes: ≤ 8 MB → the `file_read` preview channel; bigger on a LIVE KERNEL →
    the datasets data-plane on the sandbox abs path (the retain lane defers on
    a live kernel); bigger on a FINISHED target → a location-axis
    `retention.retain(dest="@workspace")` copy, which lands in the retained
    tree — the local tier serves it from then on. Cache-dir writes are ATOMIC
    (unique .partial + os.replace — a reader never sees a half-written file)
    and digest-stamped for revalidation."""
    import os as _os
    import uuid as _uuid
    from core.compute import retention
    run_id, rel = loc["run_id"], loc["rel"]
    target, site = loc["target"], loc["site"]
    size = loc.get("size") or 0
    digest = loc.get("digest") or _file_digest(loc.get("size"), loc.get("mtime"))
    dest = _safe_join(str(_fetched_cache_dir(run_id)), rel)
    if not dest:
        return None
    if _os.path.isfile(dest) and _stamp_read(dest) == digest:
        return _os.path.realpath(dest)        # a current copy is already home
    sp = progress or (lambda *_: None)
    _os.makedirs(_os.path.dirname(dest) or ".", exist_ok=True)
    if size <= _PREVIEW_CAP:
        try:
            rd = retention.file_read(target, rel, max_bytes=_PREVIEW_CAP)
        except Exception:  # noqa: BLE001
            return None
        b64 = rd.get("bytes_b64")
        if b64 is None or rd.get("truncated"):
            return None
        import base64
        tmp = f"{dest}.partial.{_os.getpid()}.{_uuid.uuid4().hex}"
        with open(tmp, "wb") as fh:
            fh.write(base64.b64decode(b64))
        _os.replace(tmp, dest)                # atomic: never a half-written dest
        _stamp_write(dest, digest)
        return _os.path.realpath(dest)
    if _is_kernel_target(target):
        abs_path = _kernel_abs_path(target, site, rel)
        if not abs_path:
            return None
        sp(f"Fetching {rel} from {site} ({size / 1e6:.0f} MB)…")
        tmp = f"{dest}.partial.{_os.getpid()}.{_uuid.uuid4().hex}"
        if _data_plane_fetch(abs_path, site, tmp, force=force) and _os.path.isfile(tmp):
            _os.replace(tmp, dest)
            _stamp_write(dest, digest)
            return _os.path.realpath(dest)
        try:
            _os.remove(tmp)
        except OSError:
            pass
        return None
    # Finished target past the preview cap: place via retention into the
    # workspace retained tree (immutable bytes — the retained tier serves the
    # copy from now on; no cache stamp needed).
    sp(f"Fetching {rel} from {site} ({size / 1e6:.0f} MB)…")
    try:
        res = retention.retain(target, include=[rel], dest="@workspace",
                               label=run_id, background=False)
    except Exception:  # noqa: BLE001
        return None
    lp = retention.location_path(res)
    if not lp:
        return None
    for cand in (_os.path.join(lp, rel), _os.path.join(lp, rel.rsplit("/", 1)[-1])):
        real = _os.path.realpath(cand)
        if _os.path.isfile(real):
            return real
    return None


def _materialize_store(loc: dict, *, force: bool = False, progress=None) -> Optional[str]:
    """Bring a remote DIRECTORY store home (the caller's gate already passed).
    LIVE KERNEL → the datasets data-plane on the sandbox abs path (retain
    defers there; the read channel caps at 8 MB); FINISHED target → a
    location-axis `retain(dest="@workspace")` into the retained tree. Data-
    plane installs are atomic (unique temp dir → swap) and stamped with the
    digest captured at LOCATE time — a store that grew during the transfer
    reads as stale on the next open, never as current. At install time a
    `dest` that already matches the CURRENT digest is kept (a concurrent open
    won) rather than destroyed — the swap can only ever replace stale bytes."""
    import os as _os
    import shutil as _sh
    import uuid as _uuid
    from core.compute import retention
    run_id, rel = loc["run_id"], loc["rel"]
    target, site = loc["target"], loc["site"]
    digest = loc.get("digest")

    if _is_kernel_target(target):
        abs_path = _kernel_abs_path(target, site, rel)
        if not abs_path:                             # unknown root or escaping name
            return None
        dest = _safe_join(str(_fetched_cache_dir(run_id)), rel)
        if not dest:
            return None
        if _os.path.isdir(dest) and digest and _stamp_read(dest) == digest:
            return _os.path.realpath(dest)           # current copy already home
        size = loc.get("size")
        sp = progress or (lambda *_: None)
        sp(f"Fetching {rel.rsplit('/', 1)[-1]} from {site}"
           + (f" ({size / 1e6:.0f} MB)…" if size else "…"))
        _os.makedirs(_os.path.dirname(dest) or ".", exist_ok=True)
        tmp = f"{dest}.partial.{_os.getpid()}.{_uuid.uuid4().hex}"
        if _data_plane_fetch(abs_path, site, tmp, force=force) and _os.path.isdir(tmp):
            # Serialize the check-then-swap for THIS dest: without the lock a
            # peer that installed while we fetched would be seen mid-install
            # (dir present, stamp not yet written) and destroyed as "stale".
            with _install_lock(dest):
                try:
                    if _os.path.isdir(dest):
                        if digest and _stamp_read(dest) == digest:
                            _sh.rmtree(tmp, ignore_errors=True)   # a peer already installed the current copy
                            return _os.path.realpath(dest)
                        trash = f"{dest}.stale.{_uuid.uuid4().hex}"
                        _os.rename(dest, trash)                   # supersede stale bytes
                        _sh.rmtree(trash, ignore_errors=True)
                    _os.replace(tmp, dest)
                    _stamp_write(dest, digest)                    # locate-time digest
                    return _os.path.realpath(dest) if _os.path.isdir(dest) else None
                except OSError:                                   # dest raced in — keep theirs
                    _sh.rmtree(tmp, ignore_errors=True)
                    return _os.path.realpath(dest) if _os.path.isdir(dest) else None
        _sh.rmtree(tmp, ignore_errors=True)
        return None                                  # failure → honest "on <site>"

    # Finished target: location-copy into the workspace retained tree
    # (immutable bytes — the retained tier serves the copy from now on).
    try:
        res = retention.retain(target, include=[rel], dest="@workspace",
                               label=run_id, background=False)
        lp = retention.location_path(res)
    except Exception:  # noqa: BLE001
        lp = None
    if lp:
        base = rel.rsplit("/", 1)[-1]
        for cand in (_os.path.join(lp, rel), _os.path.join(lp, base), lp):
            real = _os.path.realpath(cand)
            if _os.path.isdir(real):
                return real
    return None


def resolve_run_store(run_id: str, name: str, *, force: bool = False,
                      progress=None) -> Optional[str]:
    """Local path to a Run output — FILE or DIRECTORY store — matching `name`,
    bringing a remote one home when the local tiers miss. This is the
    EXPLICIT-OPEN action (a viewer launch, a deliberate store download): its
    budget is the transfer guardrail (`FETCH_GUARDRAIL_BYTES`), it reports
    fetch progress to the action's UI, and `force=True` is the user's explicit
    override past the gate. None when unresolvable or refused (the caller then
    names the site honestly); never raises."""
    from core.data.datasets import FETCH_GUARDRAIL_BYTES
    try:
        loc = locate_run_output(run_id, name)
        if not loc:
            return None
        if loc.get("local_path"):
            return loc["local_path"]
        return materialize_run_output(loc, max_bytes=FETCH_GUARDRAIL_BYTES,
                                      force=force, progress=progress)
    except Exception as e:  # noqa: BLE001 — a resolver must degrade, not raise
        _log.debug("resolve_run_store failed for %s/%s: %s", run_id, name, e)
        return None


def _sel_match(rel: str, done_sel: list):
    """meta of a remote/in-place `done` retain whose selection covers `rel` (an include
    glob matches and no exclude does), else None. Path-aware globbing (`*` stays within a
    segment) so a `*.txt` selection does NOT falsely claim `sub/deep/a.txt` — 'lose bytes,
    never lie' (§5.1). Used when a retained sidecar isn't locally readable."""
    for include, exclude, meta in done_sel:
        if any(_glob_match(rel, g) for g in (exclude or [])):
            continue
        if any(_glob_match(rel, g) for g in (include or [])):
            return meta
    return None


def run_durable_view(run_id: str) -> dict:
    """Per-file durability view for the Run's Files panel — each produced file annotated
    with WHERE it is durable, so the panel tells the truth past the sandbox sweep
    (misc/output_durability.md §6.2, §6.1b). Small surfaced files are durable in aba's
    artifact store (harvest copy → they carry a `url`); the rest depend on weft retention.

    Returns {"files": [{rel, bytes, kind, state, badge, url, site, large}], "summary": {...}}.
    States (weft-truth, decoupled from the served URL): retained | saving | in-store | at-risk |
    in-sandbox | cleared. `retained`/`saving` are weft's durability (done / pinned-pending);
    `in-store` is aba's serving cache only (a small surfaced copy, size-capped — shown honestly,
    never a fake "retained"); `at-risk` is a large output live on scratch that nothing has kept
    yet (the crown-jewel-in-danger, RED); the Keep button / plan-end retain move a file
    at-risk → saving → retained."""
    from urllib.parse import quote
    from core.exec.artifacts import artifacts_for_run
    from core.exec.run import _MAX_HARVEST_BYTES
    from core.compute import retention

    ent = get_entity(run_id)
    if not ent:
        return {"files": [], "summary": {}}
    md = ent.get("metadata") or {}

    # weft retained rows under this Run's label → done files (by relpath) + pending flag.
    # A local retained tree's `.weft-run.json` gives the exact file list. A REMOTE
    # in-place retain (a `storage_durable` site's retain.dir — §5.1: the bytes stay on
    # the site, we never move them home) has no locally-readable sidecar, so we fall back
    # to the row's retained `selection` and attribute "kept (on site)" to produced paths
    # that match it. Without this a remote-durable file would be mislabeled not-kept.
    import json as _json
    done_files: dict = {}
    done_sel: list = []          # [(include_globs, exclude_globs, meta)] for remote rows
    pending_lit: set = set()     # literal include paths of pinned-pending retains → per-file "saving"
    pending_glob: list = []      # glob include patterns of pinned-pending retains
    failed_rows = 0              # retain rows that ended `failed` — surfaced, not swallowed
    view_degraded = False        # retention index unreachable (substrate expected)
    try:
        for row in (retention.retained(label=run_id) or []):
            state = row.get("state")
            if state == "failed":
                failed_rows += 1
            if state == "pinned-pending":
                try:
                    inc = _json.loads(row.get("selection") or "{}").get("include") or []
                except Exception:  # noqa: BLE001
                    inc = []
                for g in inc:
                    if any(c in g for c in "*?["):
                        pending_glob.append(g)
                    else:
                        pending_lit.add(g)
            elif state == "done":
                loc = retention.location_path(row)
                meta = {"location": loc, "site": row.get("site"),
                        "in_place": bool(row.get("in_place"))}
                sfiles = _sidecar_files(loc)
                if sfiles:
                    for rel in sfiles:
                        done_files[rel] = meta
                else:
                    sel = {}
                    try:
                        sel = _json.loads(row.get("selection") or "{}")
                    except Exception:  # noqa: BLE001
                        pass
                    done_sel.append((sel.get("include") or [], sel.get("exclude") or [], meta))
    except Exception as e:  # noqa: BLE001
        _log.warning("durable view: retained() failed for %s: %s", run_id, e)
        # outage honesty: with the substrate CONFIGURED but the retention
        # index unreachable, kept files must NOT fall through to "discarded —
        # it was not kept" (they render as unknown below); a weft-less
        # fallback deployment stays on the normal path.
        try:
            from core.compute import adapter as _ad
            view_degraded = bool(_ad.status().get("ok"))
        except Exception:  # noqa: BLE001
            pass

    # terminal inventory paths (survive the sweep) across the Run's targets — the fallback
    # when a live stat isn't available. ONE batched call for all targets (weft bd6ae6e);
    # per-entry typed errors (no receipt yet is fine) skip that target, never the batch.
    from core.compute.errors import is_error_payload
    inv_paths: set = set()
    targets = list(md.get("weft_targets") or [])
    if targets:
        try:
            for inv in (retention.inventories(targets).get("inventories")
                        or {}).values():
                if is_error_payload(inv):
                    continue
                inv_paths.update(e.get("path")
                                 for e in (inv.get("entries") or []))
        except Exception:  # noqa: BLE001 — substrate trouble ≠ empty inventory
            pass

    # DEDUP by relpath: `artifacts_for_run` returns one row PER EXEC (ordered by
    # started_at), so a filename produced by N cells appears N times. The Files
    # panel shows one file per path, and the LATEST production is what's on disk /
    # gets retained at settlement — so keep the last occurrence per relpath (its
    # url/size/kind). Without this, rows + summary counts inflate and the tree gets
    # duplicate sibling nodes with identical paths.
    by_rel: dict = {}

    import fnmatch as _fnmatch

    def _is_saving(rel: str) -> bool:
        """A pinned-pending retain covers this path (literal include, or a glob that matches)."""
        return rel in pending_lit or any(_fnmatch.fnmatch(rel, g) for g in pending_glob)

    # §8c two-axis vocabulary (misc/more_weft_ui.md): badges say PROTECTION
    # (kept ✓ / keeping… / temporary / discarded) + location only when the bytes
    # are not simply here. State KEYS are unchanged (wire contract). While the
    # Run is OPEN, `temporary` is expressed by ABSENCE (empty badge — it's the
    # default, not news; the Keep shield is the affordance); after close it
    # becomes explicit.
    run_open = md.get("run_state") == "open"
    _CLEARED = "discarded — swept by housekeeping; it was not kept"
    _UNKNOWN = "unknown — retention storage unreachable right now"
    _TEMP = "" if run_open else "temporary — will be discarded; Keep it to save it"

    def _live(is_large: bool):
        """A produced file that exists but nothing keeps: `temporary` on the
        protection axis; large ones keep the at-risk state key (styling/lever)."""
        return ("at-risk", _TEMP) if is_large else ("in-sandbox", _TEMP)

    # Dedup by relpath — a filename produced by N cells yields N artifact rows; keep the
    # last (chronological → newest is what's on disk / retained), so the panel shows one
    # row per file and the summary counts aren't inflated (review finding #1).
    for a in artifacts_for_run(run_id):
        rel = (a.get("original_name") or "").strip()
        if rel:
            by_rel[rel] = a

    # Live on-disk check (weft run_file_stat) — authoritative for in-sandbox
    # vs cleared, and the ONLY signal on a live kernel (no terminal inventory
    # yet; the proxy would mislabel every live file "cleared"). BATCHED (weft
    # bd6ae6e): only the rels no earlier tier answers, ONE call per target.
    # The per-file loop this replaces was the convoy's amplifier — 2N store
    # queries + N subprocess spawns, serialized, under a 50-round-trip budget
    # that silently left the tail to the proxy.
    _STAT_CAP = 500          # per-request bound; files beyond it → proxy path
    need_stat = [rel for rel, a in by_rel.items()
                 if rel not in done_files and not _sel_match(rel, done_sel)
                 and not _is_saving(rel) and not a.get("url")][:_STAT_CAP]
    stat_res: dict = {}      # rel -> (performed, exists, bytes)
    if targets and need_stat:
        answered: set = set()
        found: dict = {}     # rel -> live bytes; FIRST target with the file wins
        for t in targets:
            unresolved = [r for r in need_stat if r not in found]
            if not unresolved:
                break
            try:
                ans = retention.file_stats(t, unresolved).get("files") or {}
            except Exception:  # noqa: BLE001 — target unreachable → not-checked
                continue
            for r in unresolved:
                st = ans.get(r)
                if not isinstance(st, dict):
                    continue   # unanswered (emulation cap) → stays not-checked
                answered.add(r)
                if st.get("exists"):
                    found[r] = st.get("bytes") or 0
        stat_res = {r: (r in answered, r in found, found.get(r, 0))
                    for r in need_stat}

    files = []
    counts = {"retained": 0, "saving": 0, "in_store": 0,
              "at_risk": 0, "in_sandbox": 0, "cleared": 0, "unknown": 0}
    for rel, a in by_rel.items():
        url = a.get("url")
        size = a.get("size") or a.get("bytes") or 0
        kind = a.get("kind") or "file"
        large = bool(size and size > _MAX_HARVEST_BYTES)
        site = None
        remote = False                            # bytes live on a site, not on this controller
        # Weft durability is the truth and comes FIRST — done, then pinned-pending (saving) —
        # so a file already retained by weft never mislabels as a mere serving copy. aba's
        # artifact store (`url`) is only a serving cache (in-store), below weft. Then the live
        # sandbox: a large live-but-unkept output is at-risk (RED), a small one in-sandbox.
        if rel in done_files:                     # weft retained tree, local (sidecar readable here)
            d = done_files[rel]; site = d["site"]; remote = bool(d["in_place"]) and site != "local"
            state = "retained"; badge = f"kept ✓ · on {site}" if remote else "kept ✓"
        elif (dm := _sel_match(rel, done_sel)):   # remote in-place durable retain (§5.1)
            site = dm["site"]; remote = bool(dm["in_place"]) and site != "local"
            state = "retained"
            badge = f"kept ✓ · on {site}" if remote else "kept ✓"
        elif _is_saving(rel):                     # covered by a pinned-pending weft retain
            state = "saving"
            badge = ("keeping… · keeps the version at run settlement" if large
                     else "keeping… · captured when the run settles")
        elif url:                                 # small surfaced → aba serving cache only
            state, badge = "in-store", "temporary · a viewing copy is held here"
        else:
            performed, exists, live_bytes = stat_res.get(rel, (False, False, 0))
            if exists:
                if not size and live_bytes:          # real size for a live file
                    size = live_bytes
                    large = size > _MAX_HARVEST_BYTES
                state, badge = _live(large)
            elif performed and not view_degraded:
                state, badge = "cleared", _CLEARED
            elif rel in inv_paths:
                state, badge = _live(large)          # proxy: inventoried at terminal
            elif view_degraded:
                # the retained index was unreachable — a KEPT file would land
                # here and "discarded — it was not kept" would be a lie
                state, badge = "unknown", _UNKNOWN
            else:
                state, badge = "cleared", _CLEARED
        # Served URL, decoupled from state. R4 (serve local files directly from weft, not the
        # /artifacts serving cache): a locally-RETAINED file is served straight from weft's
        # durable tier via /file — the durable copy IS the truth, so we don't route through the
        # store cache even when a store url exists. Every other state keeps the store url when
        # present (immediate + reliable during the live/saving window), else the tier-resolving
        # /file route (remote in-place fetches on open, B4). `cleared` has no bytes → no link.
        weft_url = f"/api/runs/{run_id}/file?rel={quote(rel)}"
        if state in ("cleared", "unknown"):
            view_url = None
        elif state == "retained":
            # Served straight from weft's durable tier via /file — which resolves
            # REMOTE bytes too (canonical locate + a transparent bring-home under
            # the gate, an honest site-naming 413 above it), so a remote in-place
            # keep gets a LIVE link; the `on <site>` badge still says where the
            # bytes durably live (presentation parity with the recorded truth).
            view_url = weft_url
        else:
            view_url = url or weft_url
        files.append({"rel": rel, "bytes": size, "kind": kind, "state": state,
                      "badge": badge, "url": view_url, "site": site, "large": large})
        counts[_COUNT_KEY[state]] += 1
    summary: dict = {**counts, "total": len(files)}
    if view_degraded:
        summary["degraded"] = True   # UI: badge honesty, not "discarded"
    # P1 honest surfacing (UI-only — never injected into agent context; see project
    # CLAUDE.md on shared agent inputs): declared outputs the run never produced, retain
    # rows that ended `failed`, and the last synchronous retain error recorded on the Run.
    declared = _declared_output_names(md)
    if declared:
        produced_basenames = {f["rel"].rsplit("/", 1)[-1] for f in files}
        missing = sorted(n for n in declared if n not in produced_basenames)
        if missing:
            summary["missing_declared"] = missing
    if failed_rows:
        summary["retain_failed"] = failed_rows
    if md.get("retention_alert"):
        summary["retention_alert"] = md["retention_alert"]
    return {"files": files, "summary": summary}


def run_durable_tree(run_id: str) -> dict:
    """`run_durable_view` nested into a TreeNode-compatible tree (root → folders →
    file nodes carrying the durable `state`/`badge`), for the Files panel to render
    directly. Folders sort before files, name-ascending. Carries `summary` alongside."""
    return durable_tree_from_view(run_durable_view(run_id))


def durable_tree_from_view(dv: dict) -> dict:
    """Pure view→tree transform. Split from run_durable_tree so the /durable
    route can compute the (expensive) view ONCE per coalesced flight and
    derive flat/tree shapes per request. `dv` may be shared across concurrent
    requests — this function must never mutate it."""
    root: dict = {"kind": "root", "name": "", "path": "", "children": []}
    dirs: dict = {"": root}

    def _dir(path: str) -> dict:
        if path in dirs:
            return dirs[path]
        parent_path, _, name = path.rpartition("/")
        parent = _dir(parent_path)
        node = {"kind": "folder", "name": name, "path": path, "children": []}
        parent["children"].append(node)
        dirs[path] = node
        return node

    for f in dv["files"]:
        rel = f["rel"]
        parent_path, _, name = rel.rpartition("/")
        _dir(parent_path)["children"].append({
            "kind": "file", "name": name, "path": rel,
            "size": f.get("bytes"),
            "artifact_path": f.get("url"),   # server-supplied URL (artifacts/ or /file)
            "state": f.get("state"),         # kept | pinned-pending | in-sandbox | cleared
            "badge": f.get("badge"),
            "large": f.get("large"),
            "site": f.get("site"),
            "art_kind": f.get("kind"),       # figure | table | file (artifact kind)
        })

    def _sort(node: dict) -> None:
        kids = node.get("children")
        if not kids:
            return
        kids.sort(key=lambda n: (n["kind"] != "folder", n["name"].lower()))
        for k in kids:
            _sort(k)
    _sort(root)
    return {**root, "summary": dv["summary"]}


def _auto_pin_declared_finals(run_id: str, run_metadata: dict) -> list[str]:
    """Pin artifacts produced by this Run whose filename matches any
    step's `expected_outputs` on the plan that opened the Run.

    The plan_entity_id is recorded on the Run's metadata (set by
    open_run when called with a plan_entity_id). If no plan is
    referenced, no auto-pin happens — this is the post-v1 mechanism
    for "the plan said it would produce X; surface X automatically."

    Matching: exact basename only. `expected_outputs: ["umap.png"]`
    matches any artifact with `original_name` ending in "umap.png"
    (covers per-sample subdirs like "samples/A/umap.png"). Bare
    descriptions like "DE results" don't match (no '.' extension),
    skipped silently — the agent can pin those explicitly via the
    `pin_artifact` MCP tool.

    Returns the list of (newly) pinned entity ids."""
    pinned: list[str] = []
    expected_names = _declared_output_names(run_metadata)
    if not expected_names:
        return pinned

    from core.exec.artifacts import artifacts_for_run, parse_artifact_id
    from content.bio.lifecycle.artifacts import pin_artifact
    for a in artifacts_for_run(run_id):
        leaf = (a.get("original_name") or "").rsplit("/", 1)[-1]
        if not leaf or leaf not in expected_names:
            continue
        try:
            exec_id, kind, idx = parse_artifact_id(a["artifact_id"])
            out = pin_artifact(exec_id, kind, idx,
                                wrap_in_result=True,
                                thread_id=run_metadata.get("thread_id"))
            if out.get("was_new"):
                pinned.append(out["entity_id"])
                _log.info("auto-pinned declared final %s → %s",
                          leaf, out["entity_id"])
        except Exception as e:  # noqa: BLE001
            _log.warning("auto-pin failed for %s: %s", leaf, e)
    return pinned


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def close_idle_runs(thread_id: Optional[str] = None, *,
                     idle_seconds: int = IDLE_TIMEOUT_S) -> list[str]:
    """Auto-close open Runs that haven't seen entity-write activity in
    `idle_seconds`. Scoped to a single thread if `thread_id` is given, else
    sweeps every open Run.

    Activity = the Run's row `updated_at`, which `update_entity` bumps on
    every write — child-artifact attachment, manifest refresh, code append.
    A purely-reading user gets no bumps and their Run closes cleanly.

    The closed Run keeps all its children + manifest + recorded code; only
    `run_state` flips to "closed". The next tool call in the thread starts
    a fresh ambient analysis, so there's no surprise-empty-Run failure mode.

    Idempotent + cheap — only the open-run candidate set is iterated.
    Called opportunistically by open_run() and by the home / chat poll
    routes; no separate scheduler.

    Returns the list of closed run ids."""
    closed: list[str] = []
    now = datetime.now(timezone.utc)
    threshold = idle_seconds
    for e in list_entities(type_filter="analysis", include_archived=False):
        md = e.get("metadata") or {}
        if md.get("run_state") != "open":
            continue
        if thread_id and md.get("thread_id") != thread_id:
            continue
        last = _parse_iso(e.get("updated_at"))
        if last is None:
            continue
        if (now - last).total_seconds() < threshold:
            continue
        # Closed via the same path as explicit close (so empty Runs are
        # discarded, populated Runs flip to "closed").
        try:
            rid = close_run(md.get("thread_id") or "")
            if rid:
                closed.append(rid)
        except Exception as e2:  # noqa: BLE001 — sweeper is best-effort
            _log.warning("close_idle_runs: failed to close %s: %s", e.get("id"), e2)
    return closed


def materialize_run_from_ambient(thread_id: str, title: str) -> Optional[str]:
    """Promote the thread's ambient analysis (auto-created by registry.
    _ensure_analysis when no plan-Go Run existed) into a properly-titled,
    user-visible Run.

    Used on the retroactive-pin path: a user pinning a figure from casual
    chat triggers this so the figure's parent analysis stops being
    "ambient" and becomes a navigable Run with the user-chosen title.

    Returns the Run id (== the now-promoted ambient analysis id), or None
    if there's no ambient analysis to promote. Idempotent — calling on an
    already-promoted Run just updates the title.

    Note this is logical-only — the underlying artifact_path doesn't move
    (the ambient analysis already pointed at the thread's scratch dir).
    Files stay where they were written; the Run navigation entry now
    points at them with a meaningful name.
    """
    if not thread_id:
        return None
    rid = active_run_id(thread_id)
    if not rid:
        return None
    ent = get_entity(rid)
    if not ent:
        return None
    md = dict(ent.get("metadata") or {})
    # Whether ambient or already-named, we update the title; the meaningful
    # state change is removing the `ambient` flag (patched as a single key —
    # a whole-blob write here raced the poll loop's weft_targets append).
    was_ambient = bool(md.get("ambient", False))
    update_entity(rid, title=(title or "Analysis run").strip()[:120])
    from core.graph.entities import patch_metadata
    patch_metadata(rid, {"ambient": None})
    if was_ambient:
        _log.info("materialize_run_from_ambient: promoted ambient %s for thread %s",
                  rid, thread_id)
    return rid


_FIG_EXT = {"png", "jpg", "jpeg", "svg", "webp", "gif", "pdf"}
_TAB_EXT = {"csv", "tsv"}
_MANIFEST_CAP = 24
# PDF-preview rasterization moved to core.exec.previews — shared with
# the entity lifecycle path (artifacts.py + revisions.py) so a pinned
# PDF figure renders with the same rasterizer the Run-view grid uses.
# The PREVIEW_SUFFIX constant keeps the cache filename in lockstep
# everywhere; `_pdf_thumb_path` here is kept only for the rel-path
# math in refresh_output_manifest below (the manifest builds its own
# `/api/runs/.../file` URL rather than going through `/artifacts/`).
def _pdf_thumb_path(pdf_path) -> object:
    from pathlib import Path as _P
    from core.exec.previews import PREVIEW_SUFFIX
    return _P(pdf_path).with_suffix(_P(pdf_path).suffix + PREVIEW_SUFFIX)


def _ensure_pdf_thumb(pdf_path) -> bool:
    """Thin shim → core.exec.previews. Kept so call sites read naturally
    ('ensure thumb for this PDF') without leaking the previews module
    name into runs.py's narrative."""
    from core.exec.previews import ensure_pdf_thumb_for_disk
    return ensure_pdf_thumb_for_disk(pdf_path)


def _human_size(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return (f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}")
        f /= 1024
    return f"{n} B"


def _collapse_store_members(outputs: list[dict], run_id: str) -> list[dict]:
    """Fold the members of a directory-shaped store (a `.zarr`/etc chunk tree
    is ONE logical output, not its hundreds of shards) into a single `store`
    entry. Without this the manifest is dominated by internal shard rows
    (`axes/x/zarr.json`, chunk files …) — noise for the user, and since a
    store is one artifact every shard row carried the SAME artifact_id, which
    broke per-output addressing (live 2026-07-21). The store entry keeps the
    store's own artifact_id (if any) and a member count; its href points at the
    store root so a viewer/download resolves the unit.

    No aggregate size: by this point each member's `size` has already been
    rendered human-readable ("1.2 MB"), so there are no raw byte counts left to
    sum. Showing a store without a size is the honest option — deriving one
    would mean re-statting the tree here, on a path that must stay cheap."""
    from urllib.parse import quote as _q
    stores: dict[str, dict] = {}          # store-root rel → aggregate
    kept: list[dict] = []
    for o in outputs:
        rel = o.get("label") or ""
        root = None
        for part_end in _store_root_of(rel):
            root = part_end
            break
        if root is None:
            kept.append(o)
            continue
        agg = stores.get(root)
        if agg is None:
            agg = stores[root] = {"kind": "store", "label": root,
                                  "href": f"/api/runs/{run_id}/file?rel={_q(root)}",
                                  "n_members": 0, "artifact_id": None}
        agg["n_members"] += 1
        # the row whose label IS the store root carries the store's identity
        if rel == root and o.get("artifact_id"):
            agg["artifact_id"] = o["artifact_id"]
    for root, agg in stores.items():
        if agg.get("artifact_id") is None:
            agg.pop("artifact_id", None)
        kept.append(agg)
    return kept


def _store_root_of(rel: str):
    """Yield the store-root prefix of `rel` if it lies under (or is) a
    store-suffix directory, else yield nothing. e.g. `out/x.zarr/axes/0` →
    `out/x.zarr`; `out/x.zarr` → `out/x.zarr`; `fig.png` → (nothing)."""
    parts = rel.split("/")
    for i, p in enumerate(parts):
        if p.lower().endswith(_STORE_DIR_SUFFIXES):
            yield "/".join(parts[:i + 1])
            return


def refresh_output_manifest(run_id: str, *, plot_urls_by_name: Optional[dict] = None,
                            ensure_names: Optional[list] = None) -> None:
    """Scan the Run's output directory and write a `metadata.run` manifest
    (outputs / bulk) so the Run view lists what the pipeline produced — figures
    (with thumbnails), tables, and every other file (.rds/.h5ad/…) as a
    downloadable row. Called after each cell so the Run stays current. The full
    nested directory is also browsable in the Files tree; this is the summary.

    Option B / Phase 4 augments each output entry with an `artifact_id`
    (<exec_id>:<kind>:<idx>) when the file matches an artifact recorded
    in one of the Run's exec records. Frontends can use that id to pin
    via /api/artifacts/.../pin without the legacy disk-scan-pin path.

    P2 (misc/output_serving_model.md): the HARVEST record (exec-record
    `produced[]`, via artifacts_for_run) is the primary source — for a
    weft-substrate Run the entity `artifact_path` scratch dir holds only
    exec sidecars (the outputs live in the weft jobdir / retained tree),
    so the disk scan yields nothing there. The scan is kept and unioned
    for runs whose dir IS the output tree (jupyter runs, by-reference
    imported runs — external_import.md browses those with zero copy).
    Harvest-sourced entries resolve through the tier-crossing
    /api/runs/{rid}/file route, so they keep working past the sweep.
    (Directory stores still don't appear here — catalog migration later.)
    """
    from pathlib import Path
    ent = get_entity(run_id)
    if not ent:
        return
    d = ent.get("artifact_path")
    base = Path(d) if d else None
    scan_disk = base is not None and base.exists()
    plot_urls_by_name = plot_urls_by_name or {}
    from urllib.parse import quote
    # Build a lookup from "original_name basename" → artifact_id for this
    # Run. Use the leaf name because the manifest's `label` is the rel
    # path under the Run dir, while artifacts' `original_name` came from
    # the harvester (which may or may not include subdir context). Match
    # on basename as a safe lowest common denominator; collisions across
    # subdirs prefer the most recently produced exec.
    from core.exec.artifacts import artifacts_for_run
    # Two maps: REL PATH (authoritative — a distinct output has a distinct id)
    # and basename (fallback only when the leaf is UNIQUE across the run). The
    # basename-only map collapsed every store member sharing a leaf (e.g. the
    # per-subdir `zarr.json` of a chunked store) onto ONE artifact_id, so
    # pin/dedup/address couldn't tell them apart (live 2026-07-21). Rel-path
    # first fixes that; the basename fallback is dropped for any leaf that
    # occurs more than once.
    rel_to_artifact: dict[str, str] = {}
    _leaf_counts: dict[str, int] = {}
    name_to_artifact: dict[str, str] = {}
    run_artifacts: list = []
    try:
        run_artifacts = artifacts_for_run(run_id)
        for a in run_artifacts:
            on = (a.get("original_name") or "").strip()
            if not on:
                continue
            rel_to_artifact[Path(on).as_posix()] = a["artifact_id"]
            leaf = on.rsplit("/", 1)[-1]
            _leaf_counts[leaf] = _leaf_counts.get(leaf, 0) + 1
            name_to_artifact[leaf] = a["artifact_id"]
        # a leaf that maps ambiguously must NOT lend its id to a basename match
        name_to_artifact = {k: v for k, v in name_to_artifact.items()
                            if _leaf_counts.get(k, 0) == 1}
    except Exception as e:  # noqa: BLE001 — manifest refresh must not fail
        _log.warning("refresh_output_manifest: artifact lookup failed: %s", e)

    def _artifact_for(rel: str, name: str) -> Optional[str]:
        # rel-path match is authoritative; fall back to a UNIQUE basename
        return rel_to_artifact.get(Path(rel).as_posix()) or name_to_artifact.get(name)

    def _entry(f: Path) -> Optional[dict]:
        if not f.is_file() or f.name.startswith("."):
            return None
        # Skip anything under a hidden dir too — .exec/ holds exec-record
        # sidecars (internal bookkeeping), not user-facing outputs.
        if any(p.startswith(".") for p in f.relative_to(base).parts[:-1]):
            return None
        try:
            sz = f.stat().st_size
        except OSError:
            return None
        rel = f.relative_to(base).as_posix()
        ext = f.suffix.lower().lstrip(".")
        url = f"/api/runs/{run_id}/file?rel={quote(rel)}"
        artifact_id = _artifact_for(rel, f.name)
        if ext in _FIG_EXT:
            # PDF: rasterize page 1 to a sibling preview PNG + use that
            # as the thumb URL so the Plots grid can actually render it.
            # The suffix matches PREVIEW_SUFFIX so the entity layer's
            # ensure_preview and the manifest agree on the cache filename.
            thumb_url = plot_urls_by_name.get(f.name) or url
            if ext == "pdf" and _ensure_pdf_thumb(f):
                from urllib.parse import quote as _q
                from core.exec.previews import PREVIEW_SUFFIX as _PREV
                thumb_rel = (rel + _PREV)
                thumb_url = f"/api/runs/{run_id}/file?rel={_q(thumb_rel)}"
            out: dict = {"kind": "figure", "label": rel, "thumb": thumb_url,
                         "href": url, "size": _human_size(sz)}
        elif ext in _TAB_EXT:
            out = {"kind": "table", "label": rel, "href": url, "size": _human_size(sz)}
        else:
            out = {"kind": "file", "label": rel, "href": url + "&download=1",
                   "size": _human_size(sz)}
        if artifact_id:
            out["artifact_id"] = artifact_id
        return out

    outputs: list[dict] = []
    seen_rel: set[str] = set()
    if scan_disk:
        for f in sorted(base.rglob("*")):
            e = _entry(f)
            if e:
                outputs.append(e)
                seen_rel.add(e["label"])

    def _entry_for_name(nm, *, size_bytes=None, url_hint=None) -> Optional[dict]:
        """Manifest entry for an output known by NAME (a harvest record / harvester
        result) rather than by a local stat — href through the tier-resolving
        /api/runs/{rid}/file route (retained tree → sandbox), size best-effort
        (disk if visible, else the record's)."""
        rel = Path(str(nm)).as_posix()
        if not rel or rel in seen_rel:
            return None
        name = rel.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        url = f"/api/runs/{run_id}/file?rel={quote(rel)}"
        sz = size_bytes
        if sz is None and scan_disk:
            try:
                sz = (base / rel).stat().st_size
            except OSError:
                sz = None                   # not visible → omit size, still list it
        if ext in _FIG_EXT:
            out = {"kind": "figure", "label": rel,
                   "thumb": plot_urls_by_name.get(name) or url_hint or url, "href": url}
        elif ext in _TAB_EXT:
            out = {"kind": "table", "label": rel, "href": url}
        else:
            out = {"kind": "file", "label": rel, "href": url + "&download=1"}
        if sz is not None:
            out["size"] = _human_size(sz)
        artifact_id = _artifact_for(rel, name)
        if artifact_id:
            out["artifact_id"] = artifact_id
        return out

    # P2: union the harvest-record outputs — the authoritative, durable list of
    # what the Run produced. For a weft run the disk scan above found nothing
    # (the scratch dir holds only exec sidecars), so this IS the manifest there.
    for a in run_artifacts:
        e = _entry_for_name((a.get("original_name") or "").strip(),
                            size_bytes=a.get("size") or a.get("bytes"),
                            url_hint=a.get("url"))
        if e:
            outputs.append(e)
            seen_rel.add(e["label"])
    # NFS lag: a file a compute node just wrote may be invisible to a login-node
    # readdir AND to a by-name stat (the Run dir was created here pre-submit, so
    # its empty listing + negative dentries are cached). The harvester's result
    # (result.json) is the authoritative list of what the job produced, so union
    # those names in REGARDLESS of local visibility — size best-effort, and the
    # href/thumb resolve once NFS propagates (seconds, well before a user opens
    # the Run). Without this a just-finished Slurm job shows an empty Run.
    # (Interactive callers pass already-listed names → deduped to a no-op.)
    for nm in (ensure_names or []):
        e = _entry_for_name(nm)
        if e:
            outputs.append(e)
            seen_rel.add(e["label"])
    outputs = _collapse_store_members(outputs, run_id)
    outputs.sort(key=lambda o: o["label"])
    bulk = None
    if len(outputs) > _MANIFEST_CAP:
        bulk = {"count": len(outputs), "note": f"{len(outputs)} files in the run folder"}
        # Keep figures + the first files; figures are the high-signal previews.
        figs = [o for o in outputs if o["kind"] == "figure"]
        rest = [o for o in outputs if o["kind"] != "figure"]
        outputs = (figs + rest)[:_MANIFEST_CAP]
    # patch ONLY the fields THIS writer owns (run.outputs / run.bulk) at their
    # nested paths — the previous whole-`run` write still read-modify-wrote
    # the shared object, so this poll-loop/turn-end/threadpool writer could
    # silently revert a concurrent cancel status or placement stamp on the
    # SAME key (recheck-confirmed residue of the top-level-only patch)
    from core.graph.entities import patch_metadata
    patch_metadata(run_id, {"run.outputs": outputs,
                            "run.bulk": bulk if bulk else None})


def append_run_code(run_id: str, code: str) -> None:
    """Deprecated no-op (post-cutover of misc/exec_records_and_versioning.md).

    Historically this denormalized every cell's code onto the Run entity's
    `producing_code` column. Now each cell writes an exec_records row +
    JSON sidecar via the dispatcher; the Run's aggregated code is computed
    on demand by `exec_records.aggregated_code_for_run(run_id)`.

    Kept as a no-op (rather than removed) so callers in registry.py don't
    need a coordinated migration. Safe to delete in a follow-up sweep.
    """
    return None
