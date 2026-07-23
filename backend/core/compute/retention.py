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


def file_stat(target: str, rel: str) -> dict:
    """Existence + live size/mtime of a file in a target's sandbox (weft `run_file_stat`,
    5d1c5dc): `{target, path, exists, bytes?, mtime?}`. The in-sandbox-vs-swept distinction
    the durable view needs — authoritative on-disk (the inventory only says what EXISTED,
    and a live kernel has no terminal inventory yet)."""
    return _call("run_file_stat", target, rel)


# Version-skew memo for the batched verb forms (weft bd6ae6e): a substrate
# that predates them refuses the kwarg ONCE per process, then we emulate.
_BATCH_REFUSED: set = set()
# Emulation is per-file round-trips — the exact amplifier the batch removes —
# so it keeps the pre-batch budget; rels beyond it stay UNANSWERED (absent
# from the reply = not-checked, which callers must never read as "absent on
# disk").
_EMULATE_CAP = 50


def _predates_batch(e: BaseException) -> bool:
    """True only for 'this substrate doesn't know the batched form' — an
    in-process old signature (TypeError) or a dispatcher kwarg refusal. Real
    failures (site down, bad path) must propagate, not silently degrade to
    N round-trips."""
    if isinstance(e, TypeError):
        return "unexpected keyword" in str(e)
    return (getattr(e, "code", "") == "task.invalid"
            and ("keyword" in getattr(e, "detail", "")
                 or "unknown" in getattr(e, "detail", "")))


def file_stats(target: str, rels: list) -> dict:
    """Batched `run_file_stat(target, rels=[...])`: one target resolution, one
    keep lookup, ONE stat invocation → `{"files": {rel: answer}}` with the
    single-call per-file shape and sandbox→keep precedence preserved in-batch
    (weft bd6ae6e — a polling panel was paying 2N store queries + N subprocess
    spawns, serialized; this is the O(1) form). Weft guarantees per-path
    positive markers: a partially-run probe raises retryable internal.error
    rather than reporting a file absent."""
    rels = list(rels)
    if not rels:
        return {"files": {}}
    if "run_file_stat" not in _BATCH_REFUSED:
        try:
            return _call("run_file_stat", target, rels=rels)
        except Exception as e:  # noqa: BLE001
            if not _predates_batch(e):
                raise
            _BATCH_REFUSED.add("run_file_stat")
    out: dict = {}
    for rel in rels[:_EMULATE_CAP]:
        try:
            out[rel] = file_stat(target, rel)
        except Exception:  # noqa: BLE001 — per-file trouble = unanswered
            continue
    return {"files": out}


def inventories(targets: list) -> dict:
    """Batched `run_inventory(targets=[...])` → `{"inventories": {target:
    result | typed-error dict}}` — one absent receipt never fails the batch
    (its entry carries the error; discriminate with `is_error_payload`).
    Recorded receipts only (live=True stays per-run, per weft's contract)."""
    targets = list(targets)
    if not targets:
        return {"inventories": {}}
    if "run_inventory" not in _BATCH_REFUSED:
        try:
            return _call("run_inventory", targets=targets)
        except Exception as e:  # noqa: BLE001
            if not _predates_batch(e):
                raise
            _BATCH_REFUSED.add("run_inventory")
    from core.compute.errors import ComputeError
    out: dict = {}
    for t in targets:
        try:
            out[t] = inventory(t)
        except ComputeError as e:
            out[t] = e.to_payload()
        except Exception as e:  # noqa: BLE001
            out[t] = {"error": "internal.error", "stage": "aba",
                      "detail": str(e), "retryable": True}
    return {"inventories": out}


def file_read(target: str, rel: str, max_bytes: int = 1 << 20) -> dict:
    """Size-capped base64 PREVIEW read from a target's sandbox (weft `run_file_read`): live
    or dead, path confined to the jobdir, hard-capped at 8 MB (`data.missing` on a swept
    file). A preview channel, NOT transport — big files travel via
    `data_register(path, site=) → data_fetch` (which also mints the run:<target> lineage)."""
    return _call("run_file_read", target, rel, max_bytes=max_bytes)


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
