"""Remote store range streaming — the serving-layer registry + per-chunk cache.

When a viewer launcher resolves a directory store whose bytes live on a REMOTE
site AND the substrate exposes a ranged-read verb, it does NOT materialize the
whole tree: it registers the store's remote home here and mints the SAME store
URL. A row addresses the bytes by ONE of two arms (exactly one per row):

  * RUN arm — ``{target, base_rel, site, size, digest}``: a run/kernel jobdir
    output, read via ``retention.file_read_range(target, base_rel + chunk_rel)``.
  * REF arm — ``{ref, site, size, digest}``: a by-reference dataset addressed by
    its data-plane content ref (no resolvable run required), read via
    ``retention.data_read_range(ref, rel=chunk_rel, site=site)``. The ref IS the
    tree root, so the chunk rel passes through as the member rel (no base_rel).

The store route, on a relpath that does not resolve locally, consults this
registry and serves the requested chunk file from a per-chunk cache, back-
hauling ONLY the touched chunks over whichever arm's verb the row carries. A
cached chunk file is served with Starlette's ``FileResponse`` — HTTP Range/206
for free — so the whole 2 GiB whole-fetch guardrail is never engaged
(misc/range_channel_plan.md).

Domain-neutral: this module knows targets, sites, rels and chunk files — never
the data format. Persistence is a flat per-project JSON registry under the
project runtime area, so a mapping survives a server restart without coupling to
run/entity metadata (the store_key→home map is a serving concern, not
provenance). The cache is whole-chunk-file granularity, size-capped with an
mtime sweep.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from core.config import project_root

# Total cache budget across all remote stores in a project. On install we sweep
# oldest-mtime chunk files until under this (a simple LRU-ish reclaim — chunk
# files are small and re-fetchable).
CACHE_CAP_BYTES = 512 * 1024 * 1024

# Prefetch breadth: on a chunk-grid miss, up to this many GUESSED sibling
# coordinates ride the same batched backhaul call. Wrong guesses come back as
# per-entry typed errors inside that one call — no extra round trips, no budget
# consumed — so over-guessing is cheap and under-guessing wastes floors.
PREFETCH_SIBLINGS = 24

_REG_LOCK = threading.Lock()
# Whether the deployed substrate accepts rels=[...] batch reads — flips False
# on the first TypeError (older verb signature) and every miss thereafter goes
# straight to the singular ranged loop.
_BATCH_OK: dict = {"ok": True}


# ── outcome the store route maps to a response ───────────────────────────────

@dataclass
class ChunkOutcome:
    """What the store route should return for a remote-store chunk request.
    `status=="ok"` → FileResponse(`path`); anything else → HTTPException(`http`,
    `detail`). `serve_remote_chunk` returns None (not a ChunkOutcome) when there
    is no registry entry, so the route falls through to today's 404."""
    status: str                     # "ok" | "missing" | "error" | "reject"
    path: Optional[str] = None      # cache file for status=="ok"
    http: int = 200
    detail: str = ""
    site: Optional[str] = None
    # REF-arm stores are content-addressed (a byte change mints a new ref → a
    # new store_key/URL), so their chunks may be browser-cached as immutable.
    # RUN-arm store_keys survive re-derives (digest-wipe refreshes the server
    # cache but not a browser's), so they must keep revalidating.
    immutable: bool = False


# ── confinement (own copy — core must not import the bio safe-join) ──────────

def _safe_rel(rel: str) -> Optional[str]:
    """A caller-controlled chunk rel, normalized and refused if it escapes: an
    absolute path or any `..`/empty segment → None. Pure lexical, so it is valid
    for both a local cache path and a remote sandbox rel."""
    if not rel:
        return None
    raw = str(rel).replace("\\", "/")
    if raw.startswith("/") or os.path.isabs(raw):
        return None
    parts = [seg for seg in raw.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return None
    return "/".join(parts)


# ── registry (project-scoped JSON, restart-surviving) ────────────────────────

def _registry_path(pid: str) -> str:
    # No mkdir here: this is also the LOOKUP path, and a store-route 404 on a
    # project that never streamed must not litter it with .viewer-range/.
    return os.path.join(str(project_root(pid)), ".viewer-range", "registry.json")


def _cache_root(pid: str, site: str, store_key: str) -> str:
    return os.path.join(str(project_root(pid)), ".viewer-range", "cache",
                        site, store_key)


def _load_registry(pid: str) -> dict:
    try:
        with open(_registry_path(pid)) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — absent / unreadable → empty
        return {}


def _save_registry(pid: str, data: dict) -> None:
    path = _registry_path(pid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.partial.{os.getpid()}.{uuid.uuid4().hex}"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)                        # atomic: never a half-written registry


def register_remote_store(pid: str, store_key: str, *, site: str,
                          target: Optional[str] = None,
                          base_rel: Optional[str] = None,
                          ref: Optional[str] = None,
                          size: Optional[int] = None,
                          digest: Optional[str] = None) -> None:
    """Record (or refresh) the remote home of a streamable store, as ONE of two
    arms. RUN arm: pass `target` + `base_rel`. REF arm: pass `ref`. Exactly one
    arm must be supplied — both or neither is a malformed call and is IGNORED
    (no row written, so a later lookup misses and the launcher degrades). The
    row records only that arm's addressing plus `{site, size, digest}`.

    Idempotent. When the store's freshness `digest` changed since the last
    registration (a remote re-derive), the stale per-chunk cache for this key is
    wiped so a re-derived store can never serve stale chunks. (For the ref arm
    the `store_key` already encodes the immutable content ref, so a different ref
    mints a different key and the wipe is belt-and-suspenders.) Never raises."""
    run_arm = target is not None and base_rel is not None
    ref_arm = ref is not None
    if run_arm == ref_arm:      # both arms, or neither → malformed, ignore
        return
    row = ({"target": target, "base_rel": base_rel, "site": site,
            "size": size, "digest": digest} if run_arm
           else {"ref": ref, "site": site, "size": size, "digest": digest})
    try:
        with _REG_LOCK:
            reg = _load_registry(pid)
            prev = reg.get(store_key)
            if (prev and digest and prev.get("digest")
                    and prev["digest"] != digest):
                # Wipe where the stale chunks actually LIVE — the PREVIOUS
                # site's cache root (a re-derive can move the store between
                # sites; wiping the new root would orphan the stale bytes).
                _wipe_cache(_cache_root(pid, prev.get("site") or site, store_key))
            reg[store_key] = row
            _save_registry(pid, reg)
    except Exception:  # noqa: BLE001 — registration is best-effort; the launcher degrades
        pass


def lookup_remote_store(pid: str, store_key: str) -> Optional[dict]:
    """The registered remote home for a store_key (either arm), or None. Never
    raises."""
    try:
        entry = _load_registry(pid).get(store_key)
        addressed = isinstance(entry, dict) and (entry.get("target")
                                                 or entry.get("ref"))
        return entry if addressed else None
    except Exception:  # noqa: BLE001
        return None


# ── serving (cache hit → serve; miss → backhaul-assemble → serve) ────────────

def serve_remote_chunk(pid: str, relpath: str) -> Optional[ChunkOutcome]:
    """Serve one chunk file of a REMOTE store from the per-chunk cache, back-
    hauling only on a miss. `relpath` is the store-route relpath
    (`<store_key>/<chunk_rel>`). Returns a ChunkOutcome, or None when there is no
    registry entry for the store (the route then 404s exactly as today). Never
    raises into the route."""
    store_key, _, chunk_rel = relpath.partition("/")
    if not store_key:
        return None
    entry = lookup_remote_store(pid, store_key)
    if entry is None:
        return None
    # Confinement BEFORE any backhaul (defense in depth — the route's
    # resolve_within already blocks a `..` URL; this also covers direct callers).
    safe = _safe_rel(chunk_rel)
    if safe is None:
        return ChunkOutcome("reject", http=403, detail="chunk path escapes store")
    site = entry["site"]
    immutable = entry.get("ref") is not None
    cache_file = os.path.join(_cache_root(pid, site, store_key), safe)
    if os.path.isfile(cache_file):
        return ChunkOutcome("ok", path=cache_file,      # cache hit — no backhaul
                            immutable=immutable)
    # Miss: the TARGET is served synchronously via the singular one-shot read
    # (nothing may sit between the browser and its chunk — a synchronous
    # neighborhood batch was measured to inflate first-touch latency 3-10x by
    # paying its transfer before answering). The guessed chunk-grid siblings
    # warm in a BACKGROUND batch instead: fire-and-forget, best-effort,
    # deduped — by the time the viewer walks the grid they are cache hits.
    out = _fetch_and_cache(entry, safe, cache_file, site)
    if out.status == "ok" and _BATCH_OK["ok"]:
        _spawn_prefetch(pid, entry, store_key, safe, site)
    return out


class _BatchUnsupported(Exception):
    """The deployed substrate's range verb predates rels=[...]."""


def _numeric_siblings(safe: str, k: int) -> list:
    """Guess up to `k` sibling chunk rels by advancing the TRAILING integer
    path segments (chunk-grid coordinates, e.g. c/2/7 → c/2/8, c/3/7 …).
    Format-agnostic: purely path-structural, and a wrong guess costs only a
    typed per-entry error inside the same batched call. Rels with no integer
    tail (metadata files) get no guesses."""
    parts = safe.split("/")
    coords = []
    while parts and parts[-1].isdigit():
        coords.insert(0, int(parts.pop()))
    if not coords:
        return []
    base = "/".join(parts)

    def rel_for(c):
        tail = "/".join(str(x) for x in c)
        return f"{base}/{tail}" if base else tail

    out, seen = [], {safe}
    stride = max(1, k // 2) if len(coords) > 1 else k
    for i in range(1, stride + 1):           # walk the fastest axis forward
        c = list(coords)
        c[-1] += i
        r = rel_for(c)
        if r not in seen:
            seen.add(r); out.append(r)
    if len(coords) > 1:                      # and the next-slower axis
        for i in range(1, (k - len(out)) + 1):
            c = list(coords)
            c[-2] += i
            r = rel_for(c)
            if r not in seen:
                seen.add(r); out.append(r)
            if len(out) >= k:
                break
    return out[:k]


def _retry_once(fn):
    """Run `fn`; retry ONCE on a RETRYABLE typed error that is not a semantic
    miss (weft's `internal.error` marker-less-probe class is retryable by
    contract — one retry, then the failure surfaces)."""
    from core.compute.errors import ComputeError
    try:
        return fn()
    except ComputeError as e:
        if getattr(e, "retryable", False) and e.code not in ("data.missing",
                                                             "task.invalid"):
            return fn()
        raise


# In-flight prefetch neighborhoods, keyed (pid, store_key, seed rel) — a
# browser fires ~6 parallel grid requests whose neighborhoods overlap heavily;
# one prefetch per seed is plenty and duplicates would multiply channel load.
_PREFETCH_LOCK = threading.Lock()
_PREFETCH_INFLIGHT: set = set()
# Tests flip this to run the prefetch inline (deterministic); production runs
# it on a daemon thread so it never sits between the browser and its chunk.
PREFETCH_INLINE = False


def _spawn_prefetch(pid: str, entry: dict, store_key: str, safe: str,
                    site: str) -> None:
    """Fire-and-forget neighborhood warm-up for a just-missed chunk: one
    batched call for the guessed grid siblings, installed into the cache,
    every failure silently dropped (a failed guess is a future miss, nothing
    more). Never blocks or fails the serve that triggered it."""
    guesses = _numeric_siblings(safe, PREFETCH_SIBLINGS)
    if not guesses:
        return
    key = (pid, store_key, safe)
    with _PREFETCH_LOCK:
        if key in _PREFETCH_INFLIGHT:
            return
        _PREFETCH_INFLIGHT.add(key)

    def run():
        try:
            _prefetch_siblings(pid, entry, store_key, guesses, site)
        except _BatchUnsupported:
            _BATCH_OK["ok"] = False
        except Exception:  # noqa: BLE001 — best-effort by contract
            pass
        finally:
            with _PREFETCH_LOCK:
                _PREFETCH_INFLIGHT.discard(key)

    if PREFETCH_INLINE:
        run()
    else:
        threading.Thread(target=run, name=f"chunk-prefetch-{store_key}",
                         daemon=True).start()


def _prefetch_siblings(pid: str, entry: dict, store_key: str,
                       guesses: list, site: str) -> None:
    """ONE batched backhaul call for the guessed siblings; installs whatever
    comes back. Typed per-entry errors (wrong guesses) cost nothing; rels the
    call budget deferred to `not_read` are abandoned — they are just future
    misses. Raises _BatchUnsupported on an old substrate (no rels kwarg)."""
    from core.compute import retention
    croot = _cache_root(pid, site, store_key)
    if entry.get("ref") is not None:
        ref = entry["ref"]
        call = lambda rels: retention.data_read_range(ref, rels=rels, site=site)  # noqa: E731
        to_remote = lambda r: r                                                   # noqa: E731
        from_remote = lambda r: r                                                 # noqa: E731
    else:
        target, base = entry["target"], entry["base_rel"].rstrip("/")
        call = lambda rels: retention.file_read_range(target, rels=rels)          # noqa: E731
        to_remote = lambda r: f"{base}/{r}"                                       # noqa: E731
        from_remote = lambda r: r[len(base) + 1:] if r.startswith(base + "/") else r  # noqa: E731
    want = [g for g in guesses
            if _safe_rel(g) == g and not os.path.isfile(os.path.join(croot, g))]
    if not want:
        return
    t0 = time.monotonic()
    try:
        reply = call([to_remote(r) for r in want])
    except TypeError:
        raise _BatchUnsupported()
    warmed = total = 0
    for rrel, ent in (reply.get("files") or {}).items():
        if not isinstance(ent, dict) or ent.get("error"):
            continue                          # a failed guess is free
        data = base64.b64decode(ent.get("bytes_b64") or "")
        _install_bytes(os.path.join(croot, from_remote(rrel)), data)
        warmed += 1
        total += len(data)
    _sweep_to_cap(croot)
    if warmed:
        from core.runtime import obs
        obs.emit("data", "prefetch batch", site=site,
                 summary=f"{warmed}/{len(want)} siblings warmed",
                 bytes=total, dur_ms=int((time.monotonic() - t0) * 1000))


def _install_bytes(dest: str, data: bytes) -> None:
    """Atomic whole-member install (unique temp → os.replace); best-effort —
    a failed guess install is just a future miss, and the caller re-checks the
    TARGET's file before claiming ok."""
    tmp = f"{dest}.partial.{os.getpid()}.{uuid.uuid4().hex}"
    try:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, dest)
    except OSError:
        _discard(tmp)


def _chunk_reader(entry: dict, safe: str):
    """The per-arm ranged-read closure for one chunk file — `offset -> reply`.
    RUN arm joins `base_rel + chunk_rel` and reads by target; REF arm passes the
    chunk rel THROUGH as the tree member rel (the ref IS the store root — no
    base to join) and reads by ref + site. Both return the SAME envelope, so the
    assembly loop is arm-agnostic and the error mapping is identical."""
    from core.compute import retention
    if entry.get("ref") is not None:
        ref, site = entry["ref"], entry.get("site")
        return lambda offset: retention.data_read_range(
            ref, rel=safe, offset=offset, site=site)
    target = entry["target"]
    remote_rel = entry["base_rel"].rstrip("/") + "/" + safe
    return lambda offset: retention.file_read_range(target, remote_rel, offset=offset)


def _fetch_and_cache(entry: dict, safe: str, cache_file: str,
                     site: str) -> ChunkOutcome:
    """Loop the row's ranged-read arm to assemble the WHOLE chunk file, install
    it atomically, and return an ok outcome. Typed `data.missing` → 404;
    `task.invalid` (bad rel / containment) → 404; any other backhaul/adapter
    failure (incl. the verb being absent on a downgraded substrate) → 502
    naming the site. A zero-length remote file assembles to an empty cached
    file (served 200) — the streamer keys `missing` on the TYPED error, never on
    nbytes==0. Bytes stream INTO the temp file per loop (memory stays bounded by
    the per-call cap — a store member can be arbitrarily large); an aborted
    assembly discards the temp, never leaving a partial in the cache. Racing
    writers are tolerated: each writes its own temp and os.replace is atomic, so
    a reader never sees a half-written chunk and the content is identical
    either way."""
    from core.compute.errors import ComputeError
    from core.runtime import obs
    read = _chunk_reader(entry, safe)
    os.makedirs(os.path.dirname(cache_file) or ".", exist_ok=True)
    tmp = f"{cache_file}.partial.{os.getpid()}.{uuid.uuid4().hex}"
    offset = 0
    t0 = time.monotonic()
    try:
        with open(tmp, "wb") as fh:
            while True:
                r = _retry_once(lambda: read(offset))  # noqa: B023 — offset rebinds per loop
                nbytes = int(r.get("nbytes") or 0)
                if nbytes:
                    fh.write(base64.b64decode(r.get("bytes_b64") or ""))
                    offset += nbytes
                if r.get("eof") or not r.get("capped"):
                    break
                if nbytes == 0:                  # defensive: capped-but-empty → stop
                    break
        os.replace(tmp, cache_file)
    except ComputeError as e:
        _discard(tmp)
        if e.code == "data.missing":
            return ChunkOutcome("missing", http=404,
                                detail="chunk not found on remote store", site=site)
        if e.code == "task.invalid":
            return ChunkOutcome("missing", http=404,
                                detail="invalid chunk path", site=site)
        obs.emit("data", "chunk backhaul", site=site, severity="error",
                 summary=safe, status=e.code,
                 dur_ms=int((time.monotonic() - t0) * 1000))
        return ChunkOutcome("error", http=502, site=site,
                            detail=f"chunk backhaul failed on {site}: {e.code}")
    except OSError:
        # Local cache-tree failure (disk full, permissions): the bytes may be
        # fine remotely but we cannot serve what we could not install — an
        # "ok" pointing at a missing path would 500 out of FileResponse.
        _discard(tmp)
        return ChunkOutcome("error", http=502, site=site,
                            detail="chunk cache install failed")
    except Exception as e:  # noqa: BLE001 — verb absent / adapter down → 502
        _discard(tmp)
        obs.emit("data", "chunk backhaul", site=site, severity="error",
                 summary=safe, status=type(e).__name__,
                 dur_ms=int((time.monotonic() - t0) * 1000))
        return ChunkOutcome("error", http=502, site=site,
                            detail=f"chunk backhaul unavailable on {site}: {type(e).__name__}")
    _sweep_to_cap(os.path.dirname(cache_file), keep=cache_file)
    # `missing` outcomes deliberately do NOT emit: wrong-guess 404 probes are
    # normal viewer traffic (hundreds, ~ms each) and would flood the feed.
    obs.emit("data", "chunk backhaul", site=site, summary=safe, status="ok",
             bytes=offset, dur_ms=int((time.monotonic() - t0) * 1000))
    return ChunkOutcome("ok", path=cache_file,
                        immutable=entry.get("ref") is not None)


def _discard(tmp: str) -> None:
    try:
        os.remove(tmp)
    except OSError:
        pass


def _sweep_to_cap(start_dir: str, keep: Optional[str] = None) -> None:
    """Reclaim oldest-mtime chunk files across the project cache tree until the
    total is under CACHE_CAP_BYTES. Climbs from `start_dir` to the
    `.viewer-range/cache` root, then sweeps the whole tree. `keep` is never
    evicted — it is the just-installed file the caller is about to serve (an
    over-cap single file must survive its own install). Best-effort — a sweep
    failure never breaks a serve."""
    try:
        # Find the cache root: climb to the `.viewer-range/cache` marker.
        cur = os.path.abspath(start_dir)
        root = None
        while cur and cur != os.path.dirname(cur):
            if os.path.basename(cur) == "cache" and \
                    os.path.basename(os.path.dirname(cur)) == ".viewer-range":
                root = cur
                break
            cur = os.path.dirname(cur)
        if not root or not os.path.isdir(root):
            return
        files: list[tuple[float, int, str]] = []
        total = 0
        for dp, _dirs, fns in os.walk(root):
            for fn in fns:
                fp = os.path.join(dp, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                files.append((st.st_mtime, st.st_size, fp))
                total += st.st_size
        if total <= CACHE_CAP_BYTES:
            return
        files.sort()                             # oldest mtime first
        keep_abs = os.path.abspath(keep) if keep else None
        for _m, sz, fp in files:
            if total <= CACHE_CAP_BYTES:
                break
            if keep_abs and os.path.abspath(fp) == keep_abs:
                continue
            try:
                os.remove(fp)
                total -= sz
            except OSError:
                pass
    except Exception:  # noqa: BLE001
        pass


def _wipe_cache(cache_dir: str) -> None:
    import shutil
    try:
        shutil.rmtree(cache_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass
