"""aba's doorway to weft run-output retention (weft/misc/retention.md).

Sync, worker-thread callable — thin wrappers over the compute port's retain verbs.
The split (misc/output_durability.md): **aba owns the DECISIONS** — what to retain, the
Run label, when to forget — while **weft owns the bytes, the index, placement, and GC**.

- `inventory(target)`  — the terminal listing (facts to triage on); survives the sandbox.
- `retain(target, …)`  — relocate chosen files durably (reflink/link/copy/transfer/in-place),
                          grouped under an opaque `label` (the aba Run id spans targets). On a
                          LIVE target it's a deferred pin (`pinned-pending`), captured at the
                          target's settlement; `layout="label"` mirrors the Run in the tree.
- `location_path(x)`   — normalize a retained location across weft's dict-vs-string shapes.
- `discard(target)`    — sandbox GC now (retained files + inventory survive).
- `forget(label=…)`    — reclaim the retained BYTES for a Run; inventory + records survive
                          ("lose bytes, never knowledge"). This is aba's Run-delete verb.
- `retained(label=…)`  — the central index: what's kept + where.

`target` is a weft job_id or kernel_id. Retention operates on FINISHED targets (terminal
job / stopped-or-died kernel) or a live kernel's completed-block artifact dirs. All raise
`ComputeError` on a weft error payload — callers surface the structured cause.
"""
from __future__ import annotations

from typing import Optional

from core.compute import adapter as _adapter


def _call(name: str, /, *args, **kw):
    return _adapter.get_compute().sync_call(name, *args, **kw)


def inventory(target: str, *, glob: Optional[str] = None, min_bytes: int = 0,
              max_entries: int = 5000) -> dict:
    """Recorded automatically by weft at terminal; this reads it (returns the stored
    record when the sandbox is gone — the 'what did this run produce a month later')."""
    return _call("run_inventory", target, glob=glob, min_bytes=min_bytes,
                 max_entries=max_entries)


def retain(target: str, *, include: Optional[list] = None, exclude: Optional[list] = None,
           dest: Optional[str] = None, max_gb: Optional[float] = None,
           label: Optional[str] = None, background: bool = True,
           layout: Optional[str] = None) -> dict:
    """Keep the selected files durably, grouped under `label` (the aba Run id).

    On a FINISHED target the bytes are placed now → `{files, bytes, in_place,
    location:{site,path}, state:"queued"|"done"}` (`method` lands on the `retain.done`
    event + index row, NOT this result). On a LIVE target, a selection beyond completed
    block-artifact dirs is a **deferred pin** → `{state:"pinned-pending", matched_now,
    location:{site,path}}`: the decision is durable immediately; weft captures the bytes at
    settlement (kernel stop/death, job completion, `run_discard`, `reconcile`), taking the
    file's *eventual* version. Block-artifact-dir selections on a live kernel capture
    immediately (`done`). A literal pinned path that never appears settles `failed` with a
    `retain.pin_missing` event.

    `background=False` only for a real data dependency. `layout` defaults to `"label"` when a
    `label` is given (nests `runs/<label>/<target>/` so the retained tree mirrors the Run) and
    `"target"` otherwise — weft refuses `layout="label"` without a label. NOTE the two
    `location` shapes: this result's is a dict `{site,path}`; a `retained()` index row's is a
    bare path string — read either with `location_path()`."""
    if layout is None:
        layout = "label" if label else "target"
    return _call("run_retain", target, include=include, exclude=exclude, dest=dest,
                 max_gb=max_gb, label=label, background=background, layout=layout)


def discard(target: str) -> dict:
    """Active sandbox GC now; retained files + the inventory are untouched."""
    return _call("run_discard", target)


def forget(*, target: Optional[str] = None, label: Optional[str] = None) -> dict:
    """Reclaim retained bytes (by target or label). Idempotent; forget-by-label returns
    an itemized receipt; a site-unreachable delete leaves the row `forget_pending`
    (retryable). Knowledge (inventory + records) always survives — this is NOT erasure."""
    return _call("run_forget", target=target, label=label)


def retained(*, label: Optional[str] = None, site: Optional[str] = None) -> list:
    """The central index: retained runs (optionally filtered by label / site). Rows:
    {target, site, label, location, in_place, files, bytes, method, state, retained_at} —
    `state` in {pinned-pending, queued, inflight, done, failed}; `location` is a bare path
    string here (see `location_path`)."""
    return _call("retained_runs", label=label, site=site)


def location_path(obj) -> Optional[str]:
    """Read a retained file's on-disk location across weft's two shapes: a `retain()` result
    carries `location: {site, path}` (dict); a `retained()` index row carries `location` as a
    bare path string. Accepts the wrapping result/row (reads its `location`) or a raw location
    value. Returns the path string, or None if absent."""
    if obj is None:
        return None
    loc = obj.get("location", obj) if isinstance(obj, dict) else obj
    if isinstance(loc, dict):
        return loc.get("path")
    return loc if isinstance(loc, str) else None
