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


# The one size gate for a user-initiated ship-home, shared with the automatic
# one at run close (`_no_durable_keep_policy`): never a silent multi-GB
# transfer, never a silent loss. A refusal here names the size and the lever.
SHIP_HOME_MAX_BYTES = 2 * 1024**3


def ship_home(target: str) -> dict:
    """Copy an at-risk keep's bytes into the controller workspace.

    The repair for the one state the ledger can flag and could not fix: a run
    whose outputs were kept IN PLACE on a machine that has since stopped
    declaring durable storage. Re-retains the SAME selection with
    `dest="@workspace"`, which is `put_retained`'s INSERT-OR-REPLACE path, so
    the index row moves with the bytes rather than doubling.

    Synchronous and size-gated on purpose. A background retain flips the index
    row to `in_place=0` BEFORE the copy runs, and the ledger reads that row —
    so a queued transfer would report the result safe while its bytes were
    still on the machine about to lose them. Blocking under a 2 GB cap keeps
    the answer true; a larger set gets an honest refusal naming its size.

    Never raises for an expected outcome: an already-home keep, a missing row
    and a swept sandbox are RESULTS, not exceptions — this is called from a
    button."""
    rows = [r for r in (retained() or []) if r.get("target") == target]
    if not rows:
        return {"target": target, "ok": False, "error": "unknown_keep",
                "note": "no retained record for this run output"}
    row = rows[0]
    if not row.get("in_place"):
        return {"target": target, "ok": True, "already_home": True,
                "note": "these bytes already live in the workspace"}
    size = int(row.get("bytes") or 0)
    if size > SHIP_HOME_MAX_BYTES:
        return {"target": target, "ok": False, "error": "too_large",
                "bytes": size,
                "note": (f"{size / 1e9:.1f} GB is over the {SHIP_HOME_MAX_BYTES / 1e9:.0f} GB "
                         f"copy-here cap — declare durable storage on the machine "
                         f"(Settings → Compute), or ask to ship it explicitly")}
    sel = row.get("selection") or {}
    if isinstance(sel, str):
        import json
        try:
            sel = json.loads(sel) or {}
        except Exception:  # noqa: BLE001 — an unreadable selection is not a reason
            sel = {}       # to refuse; a bare re-retain keeps everything
    label = row.get("label") or None
    try:
        out = retain(target, include=sel.get("include"),
                     exclude=sel.get("exclude"), dest="@workspace",
                     label=label, background=False,
                     layout=sel.get("layout") or ("label" if label else "target"))
    except Exception as e:  # noqa: BLE001 — surfaced, not raised: a swept
        # sandbox ("selection matched no files") is the very loss this button
        # existed to prevent, and the user needs to be told that plainly
        return {"target": target, "ok": False, "error": "ship_failed",
                "note": str(e)}
    return {"target": target, "ok": True, "moved": True,
            "files": out.get("files"), "bytes": out.get("bytes"),
            "state": out.get("state"), "location": out.get("location")}


def secure_run_keeps(label: str) -> dict:
    """Ship every AT-RISK keep of one run into the workspace — the Guide's
    repair for the ledger's one actionable state.

    Scoped to the run (`label` is the aba Run / analysis id) and to the rows
    that are genuinely at risk: still in place, on a site that no longer
    declares durable storage. Rows already home, or in place on durable
    storage, are reported and left alone — a repair that touches more than
    the thing it was asked to repair is its own incident.

    Returns a per-target receipt. Never raises: the caller is a chat turn."""
    from core.data.ledger import _durable_map  # noqa: PLC0415 — lazy: ledger reads us
    try:
        durable = _durable_map()
    except Exception as e:  # noqa: BLE001
        return {"label": label, "ok": False, "error": "durability_unknown",
                "note": f"cannot tell which machines are durable right now: {e}"}
    try:
        rows = retained(label=label) or []
    except Exception as e:  # noqa: BLE001
        return {"label": label, "ok": False, "error": "index_unavailable",
                "note": str(e)}
    if not rows:
        return {"label": label, "ok": False, "error": "no_keeps",
                "note": "this run has no retained outputs"}
    at_risk = [r for r in rows
               if r.get("in_place") and not durable.get(r.get("site") or "local")]
    if not at_risk:
        return {"label": label, "ok": True, "secured": [], "already_safe": len(rows),
                "note": "nothing at risk here — every kept file is either on "
                        "durable storage or already in the workspace"}
    results = [ship_home(r["target"]) for r in at_risk]
    done = [r for r in results if r.get("ok")]
    return {"label": label, "ok": len(done) == len(results),
            "secured": results, "moved": len(done), "attempted": len(results),
            "note": ("copied into the workspace; the ledger clears on its next read"
                     if len(done) == len(results)
                     else "some copies did not complete — see each entry")}


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


# ── ranged read (chunk-streaming backhaul) ───────────────────────────────────
# weft's per-call ranged-read clamp; a caller loops for the remainder when the
# reply is `capped`. Two doorways share this clamp and envelope: the run-keyed
# `file_read_range` (run/kernel jobdir addressing) and the ref-addressed
# `data_read_range` (data-plane content ref addressing — the ref arm).
RANGE_CAP = 16 * 1024 * 1024
_RANGE_VERB = "run_file_read_range"
DATA_RANGE_VERB = "data_read_range"
# Per-verb availability, probed once each and cached. A deployment may expose
# the run verb but NOT the ref verb (a substrate that shipped run streaming
# before the ref arm) — so each verb is probed independently, and a caller for
# the ref arm never assumes the run verb's answer.
_verb_available: dict = {}


def range_read_available(verb: str = _RANGE_VERB) -> bool:
    """Whether the DEPLOYED substrate exposes a ranged-read `verb` — the
    run-keyed `run_file_read_range` (default) or the ref-addressed
    `data_read_range` (`DATA_RANGE_VERB`). An older weft simply lacks one or
    both; each is probed once PER VERB (no round-trip; just whether the adapter
    would dispatch it) and cached, so every caller degrades to today's
    whole-fetch path uniformly. A substrate with the run verb but not the ref
    arm answers True for the former and False for the latter. False when the
    substrate is offline. Never raises."""
    cached = _verb_available.get(verb)
    if cached is None:
        try:
            weft = _adapter.get_compute().raw_controller()
            fn = getattr(type(weft), verb, None)
            cached = bool(fn is not None and getattr(fn, "_weft_tool", False))
        except Exception:  # noqa: BLE001 — offline / unwired → degrade
            cached = False
        _verb_available[verb] = cached
    return cached


def file_read_range(target: str, rel: Optional[str] = None, *, offset: int = 0,
                    length: Optional[int] = None,
                    rels: Optional[list] = None) -> dict:
    """One ranged read from a target's sandbox/keep — the chunk-streaming
    backhaul doorway (mirrors `file_read`, run-keyed addressing). Returns
    `{target, path, at, offset, nbytes, size, eof, capped, bytes_b64}` (`at` in
    {"sandbox","retained"}; `bytes_b64` "" when nbytes==0). An out-of-range
    offset is NOT an error → nbytes=0, eof=True, size present (derive 416/404
    from it). An over-cap `length` clamps with capped=True → loop for the
    remainder (cap default `RANGE_CAP`). Typed errors surface as ComputeError:
    `data.missing` (missing file / swept sandbox / vanish-race — RETRYABLE; also
    returned when bytes were expected but the file vanished, so key a streamer on
    THIS, never on nbytes==0), `task.invalid` (containment escape / bad intake).
    Raises AttributeError when the verb is ABSENT (older substrate) — probe
    `range_read_available()` first and degrade. A singular call costs ~1 app
    round-trip over a WAN (one-shot absence-or-read since weft shim v9); the
    per-chunk cache is what makes that fine.

    `rels=[...]` batches WHOLE members in ONE remote invocation (mutually
    exclusive with rel/offset/length): returns `{"files": {rel: entry |
    {"error": <code>}}, "not_read": [rels deferred at the call budget —
    loop, never silently truncated]}`. Old substrates without the batch
    raise TypeError (unexpected kwarg) — callers degrade to singular."""
    if rels is not None:
        return _call(_RANGE_VERB, target, rels=rels)
    return _call(_RANGE_VERB, target, rel, offset=offset, length=length)


def data_read_range(ref: str, rel: Optional[str] = None, *, offset: int = 0,
                    length: Optional[int] = None, site: Optional[str] = None,
                    rels: Optional[list] = None) -> dict:
    """One ranged read addressed by a DATA-PLANE content `ref` — the ref-arm
    sibling of `file_read_range` over ONE shared weft engine, with the IDENTICAL
    envelope + semantics. Returns `{ref, at, via, offset, nbytes, size, eof,
    capped, bytes_b64}` — additionally to `file_read_range`, `at` names where it
    was read (`"workspace"` or a site name) and `via` how (`"external-home"` or
    `"site-cas"`). A TREE ref takes `rel` (a member path within the tree — our
    per-chunk store shape); a FILE ref takes NO `rel` (both misuses refuse
    loudly). Resolution prefers a local workspace CAS copy (free pread) then
    registered locations; `site=` scopes where to read.

    Same past-EOF (out-of-range offset → nbytes=0, eof=True, size present),
    over-cap clamp (`capped=True` → loop for the remainder, cap `RANGE_CAP`),
    base64 payload (`bytes_b64` "" when nbytes==0), and TYPED errors as
    `file_read_range`: `data.missing` (ref vanished / GC'd — RETRYABLE; key a
    streamer on THIS, never on nbytes==0), `task.invalid` (rel-on-FILE,
    no-rel-on-TREE, containment/intake). Raises AttributeError when the verb is
    ABSENT — a deployment may have the run verb but NOT this one, so probe
    `range_read_available(DATA_RANGE_VERB)` (a distinct per-verb cache) first and
    degrade. A singular call costs ~1 app round-trip over a WAN (one-shot
    absence-or-read since weft shim v9); the per-chunk cache is what makes that
    fine.

    `rels=[...]` batches WHOLE tree members in ONE remote invocation (mutually
    exclusive with rel/offset/length): `{"files": {rel: entry | {"error":
    <code>}}, "not_read": [...]}` with the remainder deferred EXPLICITLY at the
    call budget. Old substrates raise TypeError — degrade to singular."""
    if rels is not None:
        return _call(DATA_RANGE_VERB, ref, rels=rels, site=site)
    return _call(DATA_RANGE_VERB, ref, rel=rel, offset=offset, length=length,
                 site=site)


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
