"""aba's doorway to weft run-output retention (weft/misc/retention.md).

Sync, worker-thread callable — thin wrappers over the compute port's retain verbs.
The split (misc/output_durability.md): **aba owns the DECISIONS** — what to retain, the
Run label, when to forget — while **weft owns the bytes, the index, placement, and GC**.

- `inventory(target)`  — the terminal listing (facts to triage on); survives the sandbox.
- `retain(target, …)`  — relocate chosen files durably (reflink/link/copy/transfer/in-place),
                          grouped under an opaque `label` (the aba Run id spans targets).
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
           label: Optional[str] = None, background: bool = True) -> dict:
    """Keep the selected files durably. Returns {retain_id, files, bytes, method,
    location:{site,path}, state}. `background=False` for the foreground exception
    (a real data dependency). `label` groups a Run's several targets."""
    return _call("run_retain", target, include=include, exclude=exclude, dest=dest,
                 max_gb=max_gb, label=label, background=background)


def discard(target: str) -> dict:
    """Active sandbox GC now; retained files + the inventory are untouched."""
    return _call("run_discard", target)


def forget(*, target: Optional[str] = None, label: Optional[str] = None) -> dict:
    """Reclaim retained bytes (by target or label). Idempotent; forget-by-label returns
    an itemized receipt; a site-unreachable delete leaves the row `forget_pending`
    (retryable). Knowledge (inventory + records) always survives — this is NOT erasure."""
    return _call("run_forget", target=target, label=label)


def retained(*, label: Optional[str] = None, site: Optional[str] = None) -> list:
    """The central index: retained runs (optionally filtered by label / site)."""
    return _call("retained_runs", label=label, site=site)
