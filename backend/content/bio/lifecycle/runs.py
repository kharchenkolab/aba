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
from datetime import datetime, timezone
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
             plan_entity_id: Optional[str] = None) -> str:
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
    from core.graph.derivation import manual
    rid = create_entity(
        entity_type="analysis",
        title=(title or "Analysis run").strip()[:120],
        parent_entity_id=focus_entity_id or WORKSPACE_ID,
        derivation=manual(),   # Phase 2B: a Run is opened, not derived (actor from ambient)
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
    md["run_state"] = "closed"
    update_entity(rid, metadata=md)
    return rid


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
    plan_id = run_metadata.get("plan_entity_id")
    if not plan_id:
        return pinned
    plan_ent = get_entity(plan_id)
    if not plan_ent:
        return pinned
    plan_md = plan_ent.get("metadata") or {}
    steps = plan_md.get("steps") or []
    expected_names: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        outputs = step.get("expected_outputs") or []
        if not isinstance(outputs, list):
            continue
        for o in outputs:
            if not isinstance(o, str):
                continue
            # Only treat strings that look like filenames (have a "." in
            # the last path segment) — bare descriptions don't match.
            leaf = o.rsplit("/", 1)[-1]
            if "." in leaf:
                expected_names.add(leaf)
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
    # state change is removing the `ambient` flag.
    was_ambient = bool(md.pop("ambient", False))
    update_entity(rid, title=(title or "Analysis run").strip()[:120], metadata=md)
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
    """
    from pathlib import Path
    ent = get_entity(run_id)
    if not ent:
        return
    d = ent.get("artifact_path")
    if not d:
        return
    base = Path(d)
    if not base.exists():
        return
    plot_urls_by_name = plot_urls_by_name or {}
    from urllib.parse import quote
    # Build a lookup from "original_name basename" → artifact_id for this
    # Run. Use the leaf name because the manifest's `label` is the rel
    # path under the Run dir, while artifacts' `original_name` came from
    # the harvester (which may or may not include subdir context). Match
    # on basename as a safe lowest common denominator; collisions across
    # subdirs prefer the most recently produced exec.
    from core.exec.artifacts import artifacts_for_run
    name_to_artifact: dict[str, str] = {}
    try:
        for a in artifacts_for_run(run_id):
            leaf = (a.get("original_name") or "").rsplit("/", 1)[-1]
            if leaf:
                name_to_artifact[leaf] = a["artifact_id"]
    except Exception as e:  # noqa: BLE001 — manifest refresh must not fail
        _log.warning("refresh_output_manifest: artifact lookup failed: %s", e)

    def _entry(f: Path) -> Optional[dict]:
        if not f.is_file() or f.name.startswith("."):
            return None
        try:
            sz = f.stat().st_size
        except OSError:
            return None
        rel = f.relative_to(base).as_posix()
        ext = f.suffix.lower().lstrip(".")
        url = f"/api/runs/{run_id}/file?rel={quote(rel)}"
        artifact_id = name_to_artifact.get(f.name)
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
    for f in sorted(base.rglob("*")):
        e = _entry(f)
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
        rel = Path(str(nm)).as_posix()
        if not rel or rel in seen_rel:
            continue
        name = rel.rsplit("/", 1)[-1]
        ext = ("." + rel.rsplit(".", 1)[-1]).lower() if "." in name else ""
        ext = ext.lstrip(".")
        url = f"/api/runs/{run_id}/file?rel={quote(rel)}"
        try:
            sz = (base / rel).stat().st_size
        except OSError:
            sz = None                       # not visible yet → omit size, still list it
        if ext in _FIG_EXT:
            out = {"kind": "figure", "label": rel,
                   "thumb": plot_urls_by_name.get(name) or url, "href": url}
        elif ext in _TAB_EXT:
            out = {"kind": "table", "label": rel, "href": url}
        else:
            out = {"kind": "file", "label": rel, "href": url + "&download=1"}
        if sz is not None:
            out["size"] = _human_size(sz)
        artifact_id = name_to_artifact.get(name)
        if artifact_id:
            out["artifact_id"] = artifact_id
        outputs.append(out)
        seen_rel.add(rel)
    outputs.sort(key=lambda o: o["label"])
    bulk = None
    if len(outputs) > _MANIFEST_CAP:
        bulk = {"count": len(outputs), "note": f"{len(outputs)} files in the run folder"}
        # Keep figures + the first files; figures are the high-signal previews.
        figs = [o for o in outputs if o["kind"] == "figure"]
        rest = [o for o in outputs if o["kind"] != "figure"]
        outputs = (figs + rest)[:_MANIFEST_CAP]
    meta = dict(ent.get("metadata") or {})
    run_meta = dict(meta.get("run") or {})
    run_meta["outputs"] = outputs
    if bulk:
        run_meta["bulk"] = bulk
    else:
        run_meta.pop("bulk", None)
    meta["run"] = run_meta
    update_entity(run_id, metadata=meta)


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
