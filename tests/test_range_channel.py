"""Range channel Phase 1 — remote directory-store chunk streaming.

Drives the impl directly (tmp runtime dir, generic fixtures, a FAKE ranged-read
verb — never a live substrate), in the style of tests/test_viewer_link_resolution.py.

What is guarded, and how it is ARMED:

  * The registry + per-chunk cache (`core/viewers/range_cache.py`): a miss loops
    the FAKE backhaul, assembles the whole chunk file, installs it atomically and
    serves it; a second read is a cache HIT that MUST NOT touch the backhaul. The
    fake COUNTS calls, so a hit that still back-hauls — or a miss that never
    back-hauls at all — fails (armed).
  * The store route branch (`main.pagoda3_store`): a locally-resolved file is
    served byte-identically and NEVER consults the registry (ceiling — armed with
    a sentinel over serve_remote_chunk); a local miss with a registry hit serves
    the cached chunk; a registry miss is today's 404; a `..` URL is 403 BEFORE any
    registry consult.
  * Degradation (`_register_remote_stream`): the ranged-read verb absent → None
    (materialize path), and the remote-store resolver is not even consulted.
  * The home-{site,rel} resolver (`resolve_remote_store_stream`): derives the
    ACTUAL sandbox store rel from inventory members; local / file / missing-run →
    None.

WIDE degenerate shapes covered: zero-length chunk file; capped multi-loop
assembly; data.missing mid-stream (no partial cache); vanish after size known;
task.invalid → 404; traversal rejected before any backhaul; registry entry for a
missing run; verb-absent degradation; LRU sweep ceiling; local-branch ceiling.

Run:  python tests/test_range_channel.py   (or pytest)
"""
from __future__ import annotations
import base64
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_range_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.viewers import range_cache as rc          # noqa: E402
from core.compute import retention                  # noqa: E402
from core.compute.errors import ComputeError        # noqa: E402


# ── FAKE ranged-read verb ────────────────────────────────────────────────────

def _reader(payload_by_rel: dict, calls: list, *, cap: int = 8):
    """A FAKE `file_read_range`: serves `cap` bytes per call from
    `payload_by_rel[rel]`, capped/eof set from the offset. Records every call in
    `calls`. A rel absent from the map raises the typed `data.missing` (the miss
    signal the streamer keys on — never nbytes==0)."""
    def fake(target, rel, *, offset=0, length=None):
        calls.append((rel, offset))
        data = payload_by_rel.get(rel)
        if data is None:
            raise ComputeError("data.missing", "gone", stage="weft", retryable=True)
        b = data[offset:offset + cap]
        return {"target": target, "path": rel, "at": "sandbox", "offset": offset,
                "nbytes": len(b), "size": len(data),
                "eof": offset + len(b) >= len(data),
                "capped": offset + len(b) < len(data),
                "bytes_b64": base64.b64encode(b).decode()}
    return fake


def _register(pid, store_key, *, target="krn_x", base_rel="output/s.store",
              site="siteA", size=100, digest="d1", ref=None):
    if ref is not None:
        rc.register_remote_store(pid, store_key, site=site, ref=ref,
                                 size=size, digest=digest)
    else:
        rc.register_remote_store(pid, store_key, target=target,
                                 base_rel=base_rel, site=site, size=size,
                                 digest=digest)


# ── FAKE ref-arm ranged-read verb (data_read_range) ──────────────────────────

def _data_reader(payload_by_rel: dict, calls: list, *, cap: int = 8):
    """A FAKE `data_read_range`: serves `cap` bytes per call from
    `payload_by_rel[rel]`, keyed by the TREE MEMBER rel (no base_rel — the ref
    IS the store root). Records every call as (ref, rel, site, offset). A rel
    absent from the map raises the typed `data.missing`. The envelope carries
    the ref-arm-only `at`/`via` fields on top of the shared shape."""
    def fake(ref, rel=None, *, offset=0, length=None, site=None):
        calls.append((ref, rel, site, offset))
        data = payload_by_rel.get(rel)
        if data is None:
            raise ComputeError("data.missing", "gone", stage="weft", retryable=True)
        b = data[offset:offset + cap]
        return {"ref": ref, "at": site or "workspace", "via": "site-cas",
                "offset": offset, "nbytes": len(b), "size": len(data),
                "eof": offset + len(b) >= len(data),
                "capped": offset + len(b) < len(data),
                "bytes_b64": base64.b64encode(b).decode()}
    return fake


def _register_ref(pid, store_key, *, ref="sha256:abc", site="siteA",
                  size=100, digest=None):
    rc.register_remote_store(pid, store_key, ref=ref, site=site, size=size,
                             digest=digest)


# ── _safe_rel confinement (fully generic) ────────────────────────────────────

def test_safe_rel_accepts_normal_chunk():
    assert rc._safe_rel(".zattrs") == ".zattrs"
    assert rc._safe_rel("c/0.0") == "c/0.0"
    assert rc._safe_rel("./c/./0.0") == "c/0.0"


def test_safe_rel_rejects_traversal_absolute_empty():
    for bad in ("", "..", "../etc/passwd", "c/../../x", "/etc/passwd"):
        assert rc._safe_rel(bad) is None, bad


# ── registry: roundtrip, restart-survival, miss ──────────────────────────────

def test_registry_roundtrip_and_persists_to_disk():
    pid = "reg1"
    _register(pid, "s-a1.store", base_rel="out/s.store", site="siteA")
    # persisted as a plain project-scoped file → survives a server restart
    path = rc._registry_path(pid)
    assert os.path.isfile(path)
    # a FRESH lookup reads the file (no in-process cache) — the restart-equivalent
    e = rc.lookup_remote_store(pid, "s-a1.store")
    assert e and e["target"] == "krn_x" and e["base_rel"] == "out/s.store"
    assert e["site"] == "siteA"


def test_registry_miss_returns_none():
    assert rc.lookup_remote_store("reg_none", "nope") is None


# ── serve: unregistered / traversal (both before any backhaul) ───────────────

def test_serve_unregistered_returns_none():
    # None → the route falls through to today's 404 unchanged.
    assert rc.serve_remote_chunk("s_unreg", "nope/.zattrs") is None


def test_serve_traversal_rejected_before_backhaul():
    pid = "s_trav"
    _register(pid, "s-t.store")
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({}, calls)
    try:
        out = rc.serve_remote_chunk(pid, "s-t.store/../escape")
    finally:
        retention.file_read_range = _orig
    assert out.status == "reject" and out.http == 403
    assert calls == [], "traversal must be rejected BEFORE any backhaul call"


# ── serve: miss back-hauls, hit short-circuits (ARMED) ───────────────────────

def test_cache_miss_backhauls_then_hit_short_circuits():
    pid = "s_hit"
    _register(pid, "s-h.store", base_rel="out/s.store")
    payload = b"hello-range-chunk-bytes"        # > cap → multi-loop
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({"out/s.store/c/0.0": payload}, calls)
    try:
        out = rc.serve_remote_chunk(pid, "s-h.store/c/0.0")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == payload      # correct bytes assembled
        assert len(calls) > 0, "MISS must exercise the backhaul (armed)"
        n_miss = len(calls)
        out2 = rc.serve_remote_chunk(pid, "s-h.store/c/0.0")
        assert out2.status == "ok" and out2.path == out.path
        assert len(calls) == n_miss, "cache HIT must NOT touch the backhaul"
    finally:
        retention.file_read_range = _orig


def test_capped_multiloop_assembly():
    pid = "s_cap"
    _register(pid, "s-c.store", base_rel="b")
    payload = bytes(range(30))                   # 30 bytes, cap 8 → 4 loops
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({"b/c/1.0": payload}, calls, cap=8)
    try:
        out = rc.serve_remote_chunk(pid, "s-c.store/c/1.0")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == payload
        assert len(calls) >= 4, "capped reply must LOOP for the remainder"
    finally:
        retention.file_read_range = _orig


def test_zero_length_chunk_file():
    pid = "s_zero"
    _register(pid, "s-z.store", base_rel="b")
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({"b/empty": b""}, calls)
    try:
        out = rc.serve_remote_chunk(pid, "s-z.store/empty")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == b""     # zero-length → empty cache file, 200
    finally:
        retention.file_read_range = _orig


# ── serve: typed errors ──────────────────────────────────────────────────────

def test_data_missing_returns_404_no_cache_file():
    pid = "s_miss"
    _register(pid, "s-m.store", base_rel="b", site="siteQ")
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({}, calls)     # any rel → data.missing
    try:
        out = rc.serve_remote_chunk(pid, "s-m.store/gone")
        assert out.status == "missing" and out.http == 404
        cache = os.path.join(rc._cache_root(pid, "siteQ", "s-m.store"), "gone")
        assert not os.path.exists(cache), "a missing chunk must leave NO cache file"
    finally:
        retention.file_read_range = _orig


def test_data_missing_midstream_no_partial_cache():
    # First read succeeds (capped); the file then vanishes → data.missing on the
    # SECOND read. No partial file may be cached (vanish-after-size-known too).
    pid = "s_mid"
    _register(pid, "s-md.store", base_rel="b", site="siteR")
    calls: list = []

    def fake(target, rel, *, offset=0, length=None):
        calls.append((rel, offset))
        if offset == 0:
            b = bytes(range(8))
            return {"target": target, "path": rel, "at": "sandbox", "offset": 0,
                    "nbytes": 8, "size": 40, "eof": False, "capped": True,
                    "bytes_b64": base64.b64encode(b).decode()}
        raise ComputeError("data.missing", "swept mid-stream", stage="weft", retryable=True)

    _orig = retention.file_read_range
    retention.file_read_range = fake
    try:
        out = rc.serve_remote_chunk(pid, "s-md.store/c/2.0")
        assert out.status == "missing" and out.http == 404
        cache = os.path.join(rc._cache_root(pid, "siteR", "s-md.store"), "c/2.0")
        assert not os.path.exists(cache), "a mid-stream vanish must cache NOTHING"
        assert len(calls) == 2
    finally:
        retention.file_read_range = _orig


def test_task_invalid_returns_404():
    pid = "s_ti"
    _register(pid, "s-ti.store", base_rel="b")
    _orig = retention.file_read_range

    def fake(target, rel, *, offset=0, length=None):
        raise ComputeError("task.invalid", "bad intake", stage="weft")
    retention.file_read_range = fake
    try:
        out = rc.serve_remote_chunk(pid, "s-ti.store/x")
        assert out.status == "missing" and out.http == 404
    finally:
        retention.file_read_range = _orig


def test_backhaul_error_returns_502_naming_site():
    pid = "s_err"
    _register(pid, "s-e.store", base_rel="b", site="edge9")
    _orig = retention.file_read_range

    def fake(target, rel, *, offset=0, length=None):
        raise ComputeError("internal.error", "ssh down", stage="weft", retryable=True)
    retention.file_read_range = fake
    try:
        out = rc.serve_remote_chunk(pid, "s-e.store/x")
        assert out.status == "error" and out.http == 502
        assert "edge9" in out.detail, "a backhaul failure must NAME the site"
    finally:
        retention.file_read_range = _orig


def test_verb_absent_midsession_502():
    # An AttributeError from the adapter is the verb-absent signal; on a MISS with
    # no local copy there is nothing to serve → an honest 502 naming the site.
    pid = "s_abs"
    _register(pid, "s-ab.store", base_rel="b", site="oldsite")
    _orig = retention.file_read_range

    def fake(target, rel, *, offset=0, length=None):
        raise AttributeError("WeftAdapter: 'run_file_read_range' is not a weft tool")
    retention.file_read_range = fake
    try:
        out = rc.serve_remote_chunk(pid, "s-ab.store/x")
        assert out.status == "error" and out.http == 502 and "oldsite" in out.detail
    finally:
        retention.file_read_range = _orig


# ── freshness: a digest change wipes the stale cache ─────────────────────────

def test_digest_change_wipes_cache_same_digest_keeps():
    pid = "s_dg"
    _register(pid, "s-dg.store", base_rel="b", site="siteD", digest="v1")
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({"b/c/0.0": b"AAAA"}, calls)
    try:
        out = rc.serve_remote_chunk(pid, "s-dg.store/c/0.0")
        assert os.path.isfile(out.path)
        # re-register with the SAME digest → cache kept
        _register(pid, "s-dg.store", base_rel="b", site="siteD", digest="v1")
        assert os.path.isfile(out.path), "same digest must KEEP the cache"
        # re-register with a NEW digest (remote re-derive) → cache wiped
        _register(pid, "s-dg.store", base_rel="b", site="siteD", digest="v2")
        assert not os.path.exists(out.path), "a digest change must WIPE the stale cache"
    finally:
        retention.file_read_range = _orig


# ── LRU sweep ceiling: the cache does not grow without bound ──────────────────

def test_lru_sweep_evicts_oldest():
    pid = "s_lru"
    _register(pid, "s-lru.store", base_rel="b", site="siteL")
    payloads = {f"b/c/{i}.0": bytes([i]) * 40 for i in range(6)}
    calls: list = []
    _orig = retention.file_read_range
    _cap = rc.CACHE_CAP_BYTES
    rc.CACHE_CAP_BYTES = 100                      # tiny cap → forces eviction
    retention.file_read_range = _reader(payloads, calls, cap=64)
    try:
        paths = []
        for i in range(6):
            out = rc.serve_remote_chunk(pid, f"s-lru.store/c/{i}.0")
            assert out.status == "ok"
            paths.append(out.path)
            os.utime(out.path, (1000 + i, 1000 + i))   # deterministic mtime order
        total = 0
        for _dp, _d, fns in os.walk(rc._cache_root(pid, "siteL", "s-lru.store")):
            for fn in fns:
                total += os.path.getsize(os.path.join(_dp, fn))
        assert total <= rc.CACHE_CAP_BYTES, "sweep must hold the cache under its cap"
        assert not os.path.exists(paths[0]), "the OLDEST chunk must be evicted first"
    finally:
        retention.file_read_range = _orig
        rc.CACHE_CAP_BYTES = _cap


def test_digest_change_wipes_prev_site_cache():
    # A re-derive can MOVE the store between sites; the stale chunks live under
    # the PREVIOUS site's cache root — that is what the wipe must target.
    pid = "s_dgmv"
    _register(pid, "s-mv.store", base_rel="b", site="siteA", digest="v1")
    calls: list = []
    _orig = retention.file_read_range
    retention.file_read_range = _reader({"b/c/0.0": b"AAAA"}, calls)
    try:
        out = rc.serve_remote_chunk(pid, "s-mv.store/c/0.0")
        assert os.path.isfile(out.path)
        _register(pid, "s-mv.store", base_rel="b", site="siteB", digest="v2")
        assert not os.path.exists(out.path), \
            "digest change must wipe the PREVIOUS site's stale cache"
    finally:
        retention.file_read_range = _orig


def test_sweep_never_evicts_the_just_installed_file():
    # The extreme of the tunable: a single chunk file BIGGER than the whole cap
    # must survive its own install (it is about to be served) — everything else
    # is fair game.
    pid = "s_keep"
    _register(pid, "s-k.store", base_rel="b", site="siteK")
    calls: list = []
    _orig = retention.file_read_range
    _cap = rc.CACHE_CAP_BYTES
    rc.CACHE_CAP_BYTES = 16
    retention.file_read_range = _reader({"b/small": b"x" * 8, "b/big": b"y" * 64},
                                        calls, cap=64)
    try:
        small = rc.serve_remote_chunk(pid, "s-k.store/small")
        assert small.status == "ok"
        os.utime(small.path, (1000, 1000))            # oldest → first eviction pick
        big = rc.serve_remote_chunk(pid, "s-k.store/big")   # 64 bytes > cap 16
        assert big.status == "ok"
        assert os.path.isfile(big.path), \
            "an over-cap just-installed file must NOT be swept before serving"
        assert not os.path.exists(small.path), "the other (older) file is evicted"
    finally:
        retention.file_read_range = _orig
        rc.CACHE_CAP_BYTES = _cap


def test_lookup_does_not_create_registry_dir():
    # The lookup path runs on EVERY store-route local miss — it must not litter
    # projects that never streamed.
    from core.config import project_root
    pid = "s_clean"
    assert rc.lookup_remote_store(pid, "nope") is None
    assert not os.path.exists(os.path.join(str(project_root(pid)), ".viewer-range"))


def test_aborted_assembly_leaves_no_partial_temp():
    # Bounded-memory assembly streams into a temp file; an abort must DISCARD it
    # — nothing (cache file or *.partial.*) may remain under the store's root.
    pid = "s_tmp"
    _register(pid, "s-t.store", base_rel="b", site="siteT")

    def fake(target, rel, *, offset=0, length=None):
        if offset == 0:
            b = b"z" * 8
            return {"target": target, "path": rel, "at": "sandbox", "offset": 0,
                    "nbytes": 8, "size": 40, "eof": False, "capped": True,
                    "bytes_b64": base64.b64encode(b).decode()}
        raise ComputeError("data.missing", "swept", stage="weft", retryable=True)

    _orig = retention.file_read_range
    retention.file_read_range = fake
    try:
        out = rc.serve_remote_chunk(pid, "s-t.store/c/9.9")
        assert out.status == "missing"
        root = rc._cache_root(pid, "siteT", "s-t.store")
        leftovers = [fn for _dp, _d, fns in os.walk(root) for fn in fns]
        assert leftovers == [], f"aborted assembly left files behind: {leftovers}"
    finally:
        retention.file_read_range = _orig


# ── batched backhaul + sibling prefetch ──────────────────────────────────────

def _batch_fake(payload_by_rel: dict, calls: list, *, error_rels: dict = None,
                defer_once: set = None, defer_always: set = None):
    """A FAKE range verb speaking BOTH shapes: singular (rel/offset) like
    `_reader`, and batch (`rels=[...]`) returning weft's `{"files", "not_read"}`
    envelope — absent members as typed per-entry errors, `defer_*` rels landing
    in not_read (once, or every round)."""
    err = error_rels or {}
    d_once = set(defer_once or ())
    d_always = set(defer_always or ())

    def fake(*args, rel=None, rels=None, offset=0, length=None, site=None):
        if rels is not None:
            calls.append(("batch", tuple(rels)))
            files, not_read = {}, []
            for r in rels:
                if r in d_always or r in d_once:
                    d_once.discard(r)
                    not_read.append(r)
                elif r in err:
                    files[r] = {"error": err[r]}
                elif r in payload_by_rel:
                    b = payload_by_rel[r]
                    files[r] = {"nbytes": len(b), "size": len(b), "eof": True,
                                "capped": False,
                                "bytes_b64": base64.b64encode(b).decode()}
                else:
                    files[r] = {"error": "data.missing"}
            return {"files": files, "not_read": not_read}
        r = rel if rel is not None else (args[1] if len(args) > 1 else None)
        calls.append(("singular", r, offset))
        data = payload_by_rel.get(r)
        if data is None:
            raise ComputeError("data.missing", "gone", stage="weft", retryable=True)
        b = data[offset:offset + 8]
        return {"offset": offset, "nbytes": len(b), "size": len(data),
                "eof": offset + len(b) >= len(data),
                "capped": offset + len(b) < len(data),
                "bytes_b64": base64.b64encode(b).decode()}
    return fake


def _with_batch(fake):
    """Install `fake` as the ref-arm verb with the batch lane enabled and the
    prefetch INLINE (deterministic for tests); returns a restore callable."""
    _orig, _flag = retention.data_read_range, dict(rc._BATCH_OK)
    _inline = rc.PREFETCH_INLINE
    retention.data_read_range = fake
    rc._BATCH_OK["ok"] = True
    rc.PREFETCH_INLINE = True
    def restore():
        retention.data_read_range = _orig
        rc._BATCH_OK.update(_flag)
        rc.PREFETCH_INLINE = _inline
    return restore


def test_numeric_sibling_guesses():
    got = rc._numeric_siblings("c/2/7", 8)
    assert "c/2/8" in got and "c/3/7" in got     # both axes advance
    assert "c/2/7" not in got and len(got) <= 8
    assert len(got) == len(set(got))             # no dupes
    assert rc._numeric_siblings("zarr.json", 8) == []   # no numeric tail
    assert rc._numeric_siblings("0", 4) == ["1", "2", "3", "4"]  # single axis


def test_miss_serves_target_singular_then_one_inline_prefetch():
    """TTFB honesty: the TARGET is answered by the singular one-shot path
    FIRST (nothing sits between the browser and its chunk); the guessed
    siblings ride exactly ONE batched prefetch call and later serves of them
    are cache hits with zero further backhaul (armed on the call log)."""
    pid = "s_batch1"
    _register(pid, "s-b1.store", base_rel="", site="siteB1", target=None,
              ref="dref:abc1")
    payload = {"c/0/0": b"A" * 20, "c/0/1": b"B" * 20, "c/1/0": b"C" * 20}
    calls: list = []
    restore = _with_batch(_batch_fake(payload, calls))
    try:
        out = rc.serve_remote_chunk(pid, "s-b1.store/c/0/0")
        assert out.status == "ok" and out.immutable is True
        assert open(out.path, "rb").read() == b"A" * 20
        kinds = [c[0] for c in calls]
        assert kinds[0] == "singular", "target must be served on the singular lane first"
        assert kinds.count("batch") == 1, kinds        # one neighborhood call
        batch_rels = calls[kinds.index("batch")][1]
        assert "c/0/0" not in batch_rels, "the target is never re-fetched by prefetch"
        assert "c/0/1" in batch_rels and "c/1/0" in batch_rels
        n = len(calls)
        for sib in ("c/0/1", "c/1/0"):
            o2 = rc.serve_remote_chunk(pid, f"s-b1.store/{sib}")
            assert o2.status == "ok", sib
        assert len(calls) == n, "prefetched siblings must be cache hits (armed)"
    finally:
        restore()


def test_prefetch_abandons_not_read_and_error_entries():
    # Budget-deferred guesses and wrong guesses are ABANDONED silently (future
    # misses) — exactly one batch call, no loop, and the deferred/wrong rels
    # are not cached.
    pid = "s_batch2"
    _register(pid, "s-b2.store", base_rel="", site="siteB2", target=None,
              ref="dref:abc2")
    payload = {"c/5/5": b"T" * 10, "c/5/6": b"S" * 10}
    calls: list = []
    restore = _with_batch(_batch_fake(payload, calls, defer_always={"c/5/6"}))
    try:
        out = rc.serve_remote_chunk(pid, "s-b2.store/c/5/5")
        assert out.status == "ok"
        kinds = [c[0] for c in calls]
        assert kinds.count("batch") == 1, "not_read must NOT trigger a second call"
        croot = rc._cache_root(pid, "siteB2", "s-b2.store")
        assert not os.path.exists(os.path.join(croot, "c/5/6"))   # deferred
        assert not os.path.exists(os.path.join(croot, "c/6/5"))   # wrong guess
    finally:
        restore()


def test_batch_unsupported_flips_to_singular_once():
    # Old substrate: the verb has no rels kwarg → TypeError inside the prefetch
    # → the batch lane flips OFF for the process; later misses serve singular
    # with NO further batch attempts, and the serve itself never fails.
    pid = "s_batch4"
    _register(pid, "s-b4.store", base_rel="", site="siteB4", target=None,
              ref="dref:abc4")
    attempts = {"batch": 0}
    payload = {"c/1": b"y" * 12, "c/2": b"y" * 12}
    def old_verb(*args, rel=None, offset=0, length=None, site=None, **kw):
        if "rels" in kw:
            attempts["batch"] += 1
            raise TypeError("unexpected keyword argument 'rels'")
        r = rel if rel is not None else (args[1] if len(args) > 1 else None)
        b = payload[r][offset:offset + 8]
        return {"offset": offset, "nbytes": len(b), "size": len(payload[r]),
                "eof": offset + len(b) >= len(payload[r]),
                "capped": offset + len(b) < len(payload[r]),
                "bytes_b64": base64.b64encode(b).decode()}
    restore = _with_batch(old_verb)
    try:
        assert rc.serve_remote_chunk(pid, "s-b4.store/c/1").status == "ok"
        assert rc.serve_remote_chunk(pid, "s-b4.store/c/2").status == "ok"
        assert attempts["batch"] == 1, "TypeError must flip the batch lane OFF once"
        assert rc._BATCH_OK["ok"] is False
    finally:
        restore()


def test_retryable_internal_error_retries_once():
    # weft contract: marker-less probe = internal.error RETRYABLE. One retry,
    # then the failure surfaces as 502. Ceiling: exactly 2 attempts, never 3.
    pid = "s_retry"
    _register(pid, "s-rt.store", base_rel="b", site="siteRT")   # run arm: singular lane
    payload = b"ok-bytes"
    calls = {"n": 0}
    def flaky(target, rel, *, offset=0, length=None, **kw):
        if kw.get("rels") is not None:
            raise TypeError("no rels")
        calls["n"] += 1
        if calls["n"] == 1:
            raise ComputeError("internal.error", "marker-less", stage="weft",
                               retryable=True)
        b = payload[offset:offset + 8]
        return {"offset": offset, "nbytes": len(b), "size": len(payload),
                "eof": offset + len(b) >= len(payload),
                "capped": offset + len(b) < len(payload),
                "bytes_b64": base64.b64encode(b).decode()}
    _orig, _flag = retention.file_read_range, dict(rc._BATCH_OK)
    retention.file_read_range = flaky
    rc._BATCH_OK["ok"] = False           # singular lane directly
    try:
        out = rc.serve_remote_chunk(pid, "s-rt.store/c1")
        assert out.status == "ok" and open(out.path, "rb").read() == payload
        calls["n"] = 0
        def dead(target, rel, *, offset=0, length=None, **kw):
            calls["n"] += 1
            raise ComputeError("internal.error", "marker-less", stage="weft",
                               retryable=True)
        retention.file_read_range = dead
        out2 = rc.serve_remote_chunk(pid, "s-rt.store/c2")
        assert out2.status == "error" and out2.http == 502
        assert calls["n"] == 2, "retryable = exactly ONE retry (ceiling)"
    finally:
        retention.file_read_range = _orig
        rc._BATCH_OK.update(_flag)


def test_ref_arm_outcomes_carry_immutable_run_arm_not():
    pid = "s_imm"
    _register(pid, "s-im.store", base_rel="", site="siteI", target=None,
              ref="dref:imm1")
    calls: list = []
    restore = _with_batch(_batch_fake({"m/1": b"x" * 4}, calls))
    try:
        first = rc.serve_remote_chunk(pid, "s-im.store/m/1")
        hit = rc.serve_remote_chunk(pid, "s-im.store/m/1")
        assert first.immutable is True and hit.immutable is True
    finally:
        restore()
    _register(pid, "s-run.store", base_rel="b", site="siteI")   # run arm
    calls2: list = []
    _orig, _flag = retention.file_read_range, dict(rc._BATCH_OK)
    retention.file_read_range = _reader({"b/m/1": b"q" * 4}, calls2)
    rc._BATCH_OK["ok"] = False
    try:
        out = rc.serve_remote_chunk(pid, "s-run.store/m/1")
        assert out.status == "ok" and out.immutable is False
    finally:
        retention.file_read_range = _orig
        rc._BATCH_OK.update(_flag)


def test_backhaul_emits_console_events(monkeypatch):
    """Instrumentation guard (ARMED): an actual backhaul emits exactly ONE
    `console` event carrying site+bytes+duration+status; a cache hit and a
    wrong-guess 404 emit NOTHING (flood ceiling — probe traffic is hundreds
    of ~ms misses); a failed backhaul emits severity=error with the typed
    code. Red-proven by removing the obs.emit calls in _fetch_and_cache."""
    from core.runtime import notifications
    got: list = []
    monkeypatch.setattr(notifications, "broadcast", got.append)
    pid = "s_obs"
    _register(pid, "s-obs.store", base_rel="b", site="siteOBS")
    payload = b"chunk-bytes!"
    calls: list = []
    monkeypatch.setattr(retention, "file_read_range", _reader({"b/c1": payload}, calls))
    monkeypatch.setattr(rc, "_BATCH_OK", {"ok": False})     # singular lane only
    assert rc.serve_remote_chunk(pid, "s-obs.store/c1").status == "ok"
    evs = [e for e in got if e.get("type") == "console"]
    assert len(evs) == 1, "one backhaul = one event"
    ev = evs[0]
    assert ev["category"] == "data" and ev["verb"] == "chunk backhaul"
    assert ev["site"] == "siteOBS" and ev["status"] == "ok"
    assert ev["bytes"] == len(payload) and ev["dur_ms"] >= 0
    assert ev["summary"] == "c1"
    got.clear()
    assert rc.serve_remote_chunk(pid, "s-obs.store/c1").status == "ok"   # hit
    assert rc.serve_remote_chunk(pid, "s-obs.store/nope").status == "missing"
    assert not [e for e in got if e.get("type") == "console"], \
        "cache hits and 404 probes stay silent"
    def dead(target, rel, *, offset=0, length=None, **kw):
        raise ComputeError("internal.error", "x", stage="weft", retryable=False)
    monkeypatch.setattr(retention, "file_read_range", dead)
    assert rc.serve_remote_chunk(pid, "s-obs.store/c2").status == "error"
    evs = [e for e in got if e.get("type") == "console"]
    assert len(evs) == 1 and evs[0]["severity"] == "error"
    assert evs[0]["status"] == "internal.error" and evs[0]["site"] == "siteOBS"


def test_prefetch_batch_emits_one_summary_event(monkeypatch):
    """The inline prefetch batch emits ONE `console` event summarizing the
    warm-up (count + bytes), not one per sibling (ceiling)."""
    from core.runtime import notifications
    got: list = []
    monkeypatch.setattr(notifications, "broadcast", got.append)
    pid = "s_obs2"
    _register(pid, "s-ob2.store", base_rel="", target=None, site="siteOB2",
              ref="ref-ob2")
    payload = {"c/0": b"a" * 4, "c/1": b"b" * 4, "c/2": b"c" * 4}
    calls: list = []
    restore = _with_batch(_batch_fake(payload, calls))
    try:
        assert rc.serve_remote_chunk(pid, "s-ob2.store/c/0").status == "ok"
    finally:
        restore()
    pf = [e for e in got if e.get("type") == "console"
          and e.get("verb") == "prefetch batch"]
    assert len(pf) == 1, "one batch = one summary event"
    assert pf[0]["site"] == "siteOB2" and pf[0]["bytes"] == 8   # c/1 + c/2
    assert pf[0]["summary"].startswith("2/")


def test_route_cache_headers_by_mutability(monkeypatch, tmp_path):
    from fastapi import HTTPException  # noqa: F401
    f = tmp_path / "chunk.bin"; f.write_bytes(b"zz")
    for immutable, want in ((True, "immutable"), (False, "no-cache")):
        monkeypatch.setattr(
            "core.viewers.range_cache.serve_remote_chunk",
            lambda _pid, _rel, _i=immutable: rc.ChunkOutcome(
                "ok", path=str(f), immutable=_i))
        resp = _pagoda3_store()("prj_h", "nolocal.store/m")
        cc = resp.headers.get("cache-control", "")
        assert want in cc, (immutable, cc)


# ── the store route branch (main.pagoda3_store) ──────────────────────────────

def _pagoda3_store():
    from main import pagoda3_store
    return pagoda3_store


def _mk_local_store(pid, store_key, chunk_rel, data=b"local-bytes"):
    from core.config import project_root
    root = project_root(pid)
    f = root / "pagoda3" / store_key / chunk_rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return f


def test_route_local_branch_byte_identical_and_skips_registry(monkeypatch):
    # CEILING (armed): a local file is served byte-identically and the registry
    # is NEVER consulted — a sentinel over serve_remote_chunk records any call.
    pid = "rt_local"
    f = _mk_local_store(pid, "loc.store", "c/0.0", b"LOCAL")
    seen = {"consulted": False}

    def _sentinel(*a, **k):
        seen["consulted"] = True
        return None
    monkeypatch.setattr("core.viewers.range_cache.serve_remote_chunk", _sentinel)
    resp = _pagoda3_store()(pid, "loc.store/c/0.0")
    assert getattr(resp, "path", None) == str(f)
    assert resp.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert resp.headers["Cache-Control"] == "no-cache"
    assert seen["consulted"] is False, "a local hit must NOT consult the registry"


def test_route_registry_miss_is_today_404():
    # No local file, no registry entry → the exact 404 as before the range branch.
    from fastapi import HTTPException
    pid = "rt_miss"
    try:
        _pagoda3_store()(pid, "unknown.store/c/0.0")
        assert False, "expected 404"
    except HTTPException as ex:
        assert ex.status_code == 404


def test_route_traversal_403_before_registry(monkeypatch):
    # A `..` URL is rejected by resolve_within BEFORE the registry is consulted.
    from fastapi import HTTPException
    pid = "rt_trav"
    seen = {"consulted": False}

    def _sentinel(*a, **k):
        seen["consulted"] = True
        return None
    monkeypatch.setattr("core.viewers.range_cache.serve_remote_chunk", _sentinel)
    try:
        _pagoda3_store()(pid, "s.store/../../etc/passwd")
        assert False, "expected 403"
    except HTTPException as ex:
        assert ex.status_code == 403
    assert seen["consulted"] is False


def test_route_remote_hit_serves_cached_chunk(monkeypatch):
    pid = "rt_remote"
    _register(pid, "rem.store", base_rel="out/s.store", site="siteZ")
    calls: list = []
    monkeypatch.setattr("core.compute.retention.file_read_range",
                        _reader({"out/s.store/c/0.0": b"REMOTE-CHUNK"}, calls))
    resp = _pagoda3_store()(pid, "rem.store/c/0.0")
    assert getattr(resp, "path", None) is not None
    assert open(resp.path, "rb").read() == b"REMOTE-CHUNK"
    assert resp.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    assert resp.headers["Cache-Control"] == "no-cache"
    assert len(calls) > 0


def test_route_remote_missing_is_404(monkeypatch):
    from fastapi import HTTPException
    pid = "rt_rm_miss"
    _register(pid, "rm.store", base_rel="b", site="siteZ")
    monkeypatch.setattr("core.compute.retention.file_read_range", _reader({}, []))
    try:
        _pagoda3_store()(pid, "rm.store/gone")
        assert False, "expected 404"
    except HTTPException as ex:
        assert ex.status_code == 404


def test_route_backhaul_error_is_502(monkeypatch):
    from fastapi import HTTPException
    pid = "rt_502"
    _register(pid, "e5.store", base_rel="b", site="siteBad")

    def fake(target, rel, *, offset=0, length=None):
        raise ComputeError("internal.error", "down", stage="weft")
    monkeypatch.setattr("core.compute.retention.file_read_range", fake)
    try:
        _pagoda3_store()(pid, "e5.store/x")
        assert False, "expected 502"
    except HTTPException as ex:
        assert ex.status_code == 502
        assert "siteBad" in str(ex.detail)


# ── home-{site,rel} resolver (resolve_remote_store_stream) ───────────────────

def test_resolve_remote_store_stream_gate_aligned_rel(monkeypatch):
    # The derivation must use the SAME leading-segment rule as the gate that
    # classified the store (`_rel_under_store`): a COLLIDING interior path
    # (`aux/<basename>/…`, sorted FIRST) must NOT steal the derivation — the
    # registry would point the backhaul at a directory whose digest/size were
    # never what got registered.
    import content.bio.lifecycle.runs as runs
    monkeypatch.setattr(runs, "locate_run_output", lambda rid, name, **k: {
        "locality": "remote", "kind": "dir", "target": "krn_r", "site": "siteA",
        "size": 9999, "digest": "dd", "rel": name})
    monkeypatch.setattr(runs, "_live_inventory", lambda t, **k: {"entries": [
        {"path": "aux/foo.store/decoy"},          # interior collision, listed first
        {"path": "foo.store/meta.json"},
        {"path": "foo.store/c/0.0"}]})
    home = runs.resolve_remote_store_stream("run_1", "foo.store")
    assert home == {"target": "krn_r", "site": "siteA",
                    "store_rel": "foo.store", "size": 9999, "digest": "dd"}


def test_resolve_remote_store_stream_full_rel_name(monkeypatch):
    # Gate parity for a path-shaped name: members under the FULL rel resolve to
    # that rel (the `p == n or p.startswith(n + "/")` arm of the gate's rule).
    import content.bio.lifecycle.runs as runs
    monkeypatch.setattr(runs, "locate_run_output", lambda rid, name, **k: {
        "locality": "remote", "kind": "dir", "target": "krn_r", "site": "siteA",
        "size": 7, "digest": "dg", "rel": name})
    monkeypatch.setattr(runs, "_live_inventory", lambda t, **k: {"entries": [
        {"path": "sub/foo.store/meta.json"}]})
    home = runs.resolve_remote_store_stream("run_1", "sub/foo.store")
    assert home and home["store_rel"] == "sub/foo.store"


def test_resolve_remote_store_stream_interior_only_is_none(monkeypatch):
    # A store visible ONLY as an interior path is not confirmable by the gate's
    # rule → None → the launcher falls back to the materialize path (the
    # nested-store Known gap, run-outputs.md). The old anywhere-in-the-path
    # matcher would have registered `aux/foo.store` here.
    import content.bio.lifecycle.runs as runs
    monkeypatch.setattr(runs, "locate_run_output", lambda rid, name, **k: {
        "locality": "remote", "kind": "dir", "target": "krn_r", "site": "siteA",
        "size": 9, "digest": "dg", "rel": name})
    monkeypatch.setattr(runs, "_live_inventory", lambda t, **k: {"entries": [
        {"path": "aux/foo.store/meta.json"},
        {"path": "aux/foo.store/c/0.0"}]})
    assert runs.resolve_remote_store_stream("run_1", "foo.store") is None


def test_resolve_remote_store_stream_local_and_file_are_none(monkeypatch):
    import content.bio.lifecycle.runs as runs
    # local output → None (streaming is for remote stores)
    monkeypatch.setattr(runs, "locate_run_output", lambda rid, name, **k: {
        "locality": "local", "kind": "dir", "local_path": "/x", "site": "local"})
    assert runs.resolve_remote_store_stream("r", "foo.store") is None
    # remote FILE (not a dir store) → None
    monkeypatch.setattr(runs, "locate_run_output", lambda rid, name, **k: {
        "locality": "remote", "kind": "file", "target": "krn_r", "site": "siteA"})
    assert runs.resolve_remote_store_stream("r", "foo.h5ad") is None


def test_resolve_remote_store_stream_missing_run_is_none(monkeypatch):
    import content.bio.lifecycle.runs as runs
    monkeypatch.setattr(runs, "locate_run_output", lambda rid, name, **k: None)
    assert runs.resolve_remote_store_stream("run_gone", "foo.store") is None


# ── pre-flight note: ONE stream-or-fetch decision for BOTH branches ──────────

def _entity_fixture():
    return {"id": "ent_1", "artifact_path": "/remote/siteA/x/data.store",
            "metadata": {}}


def _patch_note_deps(monkeypatch, *, verb, home, total_bytes, seen):
    monkeypatch.setattr("content.bio.data_location.dataset_location",
                        lambda e: {"remote": True, "site": "siteA",
                                   "total_bytes": total_bytes})
    monkeypatch.setattr("content.bio.lifecycle.runs.run_id_for_entity",
                        lambda eid: "run_7")
    monkeypatch.setattr("core.compute.retention.range_read_available",
                        lambda: verb)

    def _resolver(rid, name):
        seen.append((rid, name))
        return home
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        _resolver)


def test_entity_note_streams_when_available(monkeypatch):
    # An entity-backed remote DIR STORE with streaming available gets the
    # streams-on-demand note — and the over-gate refuse wording must NOT appear
    # even for an over-gate size (streaming makes the gate irrelevant).
    from content.bio.tools.viewers import _entity_location_note
    seen: list = []
    _patch_note_deps(monkeypatch, verb=True, total_bytes=3 * 1024**3, seen=seen,
                     home={"target": "krn_1", "site": "siteA",
                           "store_rel": "data.store", "size": 3 * 1024**3,
                           "digest": "d"})
    note = _entity_location_note(_entity_fixture())
    assert note and "stream on demand" in note.lower(), note
    assert "OVER the transfer gate" not in note and "refuse" not in note, note
    assert "mirror the dataset locally" in note        # lever kept
    assert seen == [("run_7", "data.store")]           # launcher-parity derivation


def test_entity_note_fetch_wording_when_verb_absent(monkeypatch):
    # CEILING (armed): verb absent → the resolver is NOT consulted and the
    # wording is exactly today's fetch / over-gate text.
    from content.bio.tools.viewers import _entity_location_note
    seen: list = []
    _patch_note_deps(monkeypatch, verb=False, total_bytes=400_000_000, seen=seen,
                     home={"target": "krn_1", "site": "siteA",
                           "store_rel": "data.store"})
    note = _entity_location_note(_entity_fixture())
    assert note and "opening fetches it" in note, note
    assert "stream" not in note.lower()
    assert seen == [], "verb-absent must NOT consult the remote resolver"
    # over-gate size keeps the refuse wording (unchanged from today)
    seen2: list = []
    _patch_note_deps(monkeypatch, verb=False, total_bytes=3 * 1024**3, seen=seen2,
                     home=None)
    note2 = _entity_location_note(_entity_fixture())
    assert note2 and "OVER the transfer gate" in note2, note2
    assert seen2 == []


def test_note_helpers_called_only_from_shared_decision():
    # Structural census: the wording helpers are reachable ONLY through the
    # shared `_remote_note` decision — a branch calling `_remote_open_note` /
    # `_remote_stream_note` directly is streaming-blind (the V3 class).
    import ast
    import inspect
    import content.bio.tools.viewers as tv
    tree = ast.parse(inspect.getsource(tv))
    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_remote_open_note", "_remote_stream_note")
                    and fn.name != "_remote_note"):
                offenders.append(f"{fn.name}() calls {node.func.id} directly")
    assert not offenders, offenders


# ── launcher degradation + registration (_register_remote_stream) ────────────

def test_register_remote_stream_degrades_when_verb_absent(monkeypatch):
    # ARMED: with the verb absent the remote-store resolver must NOT even be
    # consulted — a sentinel records any call — and the result is None (the
    # launcher then takes today's materialize path).
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr("core.compute.retention.range_read_available", lambda: False)
    seen = {"resolved": False}

    def _sentinel(*a, **k):
        seen["resolved"] = True
        return {"target": "x", "site": "s", "store_rel": "r"}
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream", _sentinel)
    key = p3._register_remote_stream(
        {"run_id": "run_1", "name": "foo.store",
         "artifact_path": "foo.store"}, "deg1")
    assert key is None
    assert seen["resolved"] is False, "verb-absent must short-circuit before resolving"


def test_register_remote_stream_registers_and_returns_key(monkeypatch):
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr("core.compute.retention.range_read_available", lambda: True)
    monkeypatch.setattr(
        "content.bio.lifecycle.runs.resolve_remote_store_stream",
        lambda rid, name: {"target": "krn_z", "site": "siteA",
                           "store_rel": "output/foo.store",
                           "size": 42, "digest": "dg"})
    pid = "reg_launch"
    key = p3._register_remote_stream(
        {"run_id": "run_9", "name": "foo.store",
         "artifact_path": "work/foo.store"}, pid)
    # The key's suffix is the launcher's canonical store extension (its own
    # naming scheme, pinned by the launcher tests) — here we pin only that the
    # key derives from the output name and round-trips through the registry.
    assert key and key.startswith("foo-")
    e = rc.lookup_remote_store(pid, key)
    assert e and e["target"] == "krn_z" and e["site"] == "siteA"
    assert e["base_rel"] == "output/foo.store"


# ── stale-mirror shadow: bare grafted folders must not starve the launch ─────
# The live case: a store fetched into work/<run>-fetched/ grafts into the files
# tree; its FOLDER node used to carry neither run_id nor artifact_path, yet won
# the basename match in _resolve_files_node — starving BOTH launcher arms
# (streaming got no run, materialize got no path → terminal 404). Two fixes,
# both guarded: disk-grafted folders carry their real path (source honesty);
# an address-less node is NON-TERMINAL in _resolve_files_node.

def _root(*nodes):
    return {"kind": "root", "name": "", "path": "", "children": list(nodes)}


def _mk_fetched_store(tmp_path):
    """A generic fetched-mirror layout: work/run_x1-fetched/data.store/{...}."""
    store = tmp_path / "work" / "run_x1-fetched" / "data.store"
    (store / "c").mkdir(parents=True)
    (store / "meta.json").write_text("{}")
    (store / "c" / "0.0").write_bytes(b"chunk-bytes")
    return store


def test_graft_dir_folders_carry_their_disk_path(tmp_path):
    # Source honesty: every folder _graft_dir creates knows its real on-disk
    # location (nested levels each carry their OWN path, not the base's).
    from content.bio.files.tree import _graft_dir, _folder
    store = _mk_fetched_store(tmp_path)
    parent = _folder("scratch", path="working/scratch", kind="folder")
    n = _graft_dir(parent, tmp_path / "work", ephemeral=True)
    assert n == 2                                   # meta.json + c/0.0
    by_path = {}

    def walk(node):
        by_path[node["path"]] = node
        for c in node.get("children", []):
            walk(c)
    walk(parent)
    f1 = by_path["working/scratch/run_x1-fetched/data.store"]
    f2 = by_path["working/scratch/run_x1-fetched/data.store/c"]
    assert f1["kind"] == "folder" and f1["artifact_path"] == str(store)
    assert f2["kind"] == "folder" and f2["artifact_path"] == str(store / "c")


def test_shadow_mirror_resolves_local_serve(monkeypatch, tmp_path):
    # (a) THE live shape: fetched bytes exist → the launch node carries the
    # mirror's real path and the launcher resolves it LOCALLY — terminal, no
    # detour (armed: the run-output resolver must NOT be consulted).
    from content.bio.files.tree import _graft_dir, _folder
    import content.bio.web.routes.viewers as vr
    import content.bio.viewers.launchers.pagoda3 as p3
    store = _mk_fetched_store(tmp_path)
    parent = _folder("scratch", path="working/scratch", kind="folder")
    _graft_dir(parent, tmp_path / "work", ephemeral=True)
    monkeypatch.setattr("content.bio.data_location.entity_for_path", lambda p: None)
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **k: _root(parent))
    seen = {"resolver": 0}

    def _sentinel(path, **k):
        seen["resolver"] += 1
        return None
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_project_run_output",
                        _sentinel)
    node = vr._resolve_files_node(None, "data.store")
    assert node.get("artifact_path") == str(store), node
    assert seen["resolver"] == 0, "a local mirror must be terminal (LOCAL-FIRST)"
    src = p3._resolve_source(node, "shadow1")
    assert src == store                              # real local bytes serve


def test_shadow_no_mirror_falls_through_to_streaming(monkeypatch, tmp_path):
    # (b) No fetched bytes: a bare folder node (address-less — the shadow
    # shape) is NON-TERMINAL; resolution continues to the run-output marker and
    # the launch's streaming arm engages on it (armed: sentinels count both).
    import content.bio.web.routes.viewers as vr
    import content.bio.viewers.launchers.pagoda3 as p3
    bare = {"kind": "folder", "name": "data.store", "children": [],
            "path": "working/scratch/run_x1-fetched/data.store",
            "entity_id": None, "entity_type": None, "title": None}
    monkeypatch.setattr("content.bio.data_location.entity_for_path", lambda p: None)
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **k: _root(bare))
    calls = {"resolver": 0}

    def _resolver(path, **k):
        calls["resolver"] += 1
        return ("run_9", "data.store")               # remote marker
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_project_run_output",
                        _resolver)
    node = vr._resolve_files_node(None, "data.store")
    assert calls["resolver"] == 1, "a bare graft node must be NON-TERMINAL"
    assert node.get("run_id") == "run_9"
    # ...and the streaming arm receives THAT node (the graft no longer blocks it)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("x")
    monkeypatch.setattr(p3, "pagoda3_dist_path", lambda: dist)
    reg = {"node": None}

    def _register(n, pid):
        reg["node"] = n
        return "data-k1.store"
    monkeypatch.setattr(p3, "_register_remote_stream", _register)
    res = p3.launch(node, {"project_id": "shadow2"})
    assert "data-k1.store" in res.url
    assert reg["node"] is not None and reg["node"].get("run_id") == "run_9"


def test_unrelated_tree_nodes_unchanged(monkeypatch):
    # (c) Ceiling: entity-backed and disk-addressed nodes stay TERMINAL —
    # returned as-is, resolver never consulted.
    import content.bio.web.routes.viewers as vr
    fnode = {"kind": "file", "name": "table.csv", "size": 3,
             "path": "working/scratch/table.csv", "artifact_path": "/abs/table.csv"}
    enode = {"kind": "folder", "name": "data.store", "children": [],
             "path": "datasets/ds1", "entity_id": None, "title": None}
    enode2 = dict(enode, path="datasets/ds2", name="other.store", entity_id="ds_2",
                  entity_type="dataset")
    monkeypatch.setattr("content.bio.data_location.entity_for_path", lambda p: None)
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **k: _root(fnode, enode2))
    seen = {"resolver": 0}

    def _sentinel(path, **k):
        seen["resolver"] += 1
        return None
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_project_run_output",
                        _sentinel)
    assert vr._resolve_files_node(None, "table.csv") is fnode
    assert vr._resolve_files_node(None, "other.store") is enode2
    assert seen["resolver"] == 0


# ── terminal-error honesty bridge (entity-remote facts → remote wording) ─────
# The launch page's mirror lever keys on THIS regex over the error text plus an
# entity id (core/viewers/launch_page.py). The bridge's whole point is matching
# it, so the guard tests against the same expression — wording drift that
# un-matches the lever fails here.
_REMOTEISH = __import__("re").compile(
    r"lives on|bring it home|not on this machine|remote site", __import__("re").I)


def _patch_launch_shell(monkeypatch, tmp_path, *, resolved):
    """Wire `launch()` up to its terminal-error site: dist present (no module
    install), streaming registration a miss, `_resolve_source` returning
    `resolved` — so the test exercises the REAL failure branch in launch()."""
    import content.bio.viewers.launchers.pagoda3 as p3
    dist = tmp_path / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("x")
    monkeypatch.setattr(p3, "pagoda3_dist_path", lambda: dist)
    monkeypatch.setattr(p3, "_register_remote_stream", lambda node, pid: None)
    monkeypatch.setattr(p3, "_resolve_source",
                        lambda node, pid, sp=None: Path(resolved))
    return p3


def test_launch_terminal_error_names_home_site_for_by_ref_remote(monkeypatch, tmp_path):
    # THE bridge case: entity-backed, by-reference remote home, every resolver
    # tier missed (run unresolvable) → the error must match the launch page's
    # remote regex AND name the entity's home site, so the mirror lever engages.
    p3 = _patch_launch_shell(monkeypatch, tmp_path, resolved="/nonexistent/x/data.store")
    monkeypatch.setattr("core.graph.entities.get_entity", lambda eid: {
        "id": eid, "metadata": {"home": {"site": "siteA", "path": "/r/data.store"},
                                "by_reference": True}})
    try:
        p3.launch({"entity_id": "ds_1", "name": "data.store",
                   "artifact_path": "/r/data.store"}, {"project_id": "brg1"})
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as ex:
        msg = str(ex)
    assert _REMOTEISH.search(msg), f"error must engage the mirror lever: {msg!r}"
    assert "siteA" in msg, msg
    assert "source not found" not in msg, msg


def test_launch_terminal_error_generic_shape_ceilings(monkeypatch, tmp_path):
    # CEILING (a): a non-by-reference entity and a no-entity node keep the EXACT
    # generic wording (byte-identical to the pre-bridge raise).
    p3 = _patch_launch_shell(monkeypatch, tmp_path, resolved="/nonexistent/x/data.store")
    # non-by-reference entity (local, workspace-managed)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: {"id": eid, "metadata": {}})
    for node in ({"entity_id": "ds_2", "name": "data.store"},   # entity, not by-ref
                 {"name": "data.store"}):                        # no entity at all
        try:
            p3.launch(node, {"project_id": "brg2"})
            assert False, "expected FileNotFoundError"
        except FileNotFoundError as ex:
            assert str(ex) == "pagoda3: source not found for 'data.store'", str(ex)
    # by-reference but LOCAL home (site unset ⇒ local): generic too; and the
    # entity itself GONE (hard-deleted alongside the run): generic, never a raise
    for ent in ({"id": "x", "metadata": {"by_reference": True}}, None):
        monkeypatch.setattr("core.graph.entities.get_entity",
                            lambda eid, _e=ent: _e)
        try:
            p3.launch({"entity_id": "ds_3", "name": "data.store"}, {"project_id": "brg2"})
            assert False, "expected FileNotFoundError"
        except FileNotFoundError as ex:
            assert str(ex) == "pagoda3: source not found for 'data.store'", str(ex)


def test_by_ref_remote_with_local_mirror_resolves_locally(monkeypatch, tmp_path):
    # CEILING (b): a by-reference dataset WITH a working local mirror resolves
    # through the local tiers exactly as today — the bridge only rewords the
    # TERMINAL error of an already-failed resolution, it adds no tier. ARMED:
    # get_entity is a sentinel; a local hit must not even consult the entity.
    import content.bio.viewers.launchers.pagoda3 as p3
    store = tmp_path / "mirror" / "data.store"
    store.mkdir(parents=True)
    (store / "meta.json").write_text("{}")
    seen = {"entity": False}

    def _sentinel(eid):
        seen["entity"] = True
        return {"id": eid, "metadata": {"home": {"site": "siteA", "path": "/r/d"},
                                        "by_reference": True}}
    monkeypatch.setattr("core.graph.entities.get_entity", _sentinel)
    src = p3._resolve_source({"entity_id": "ds_4", "name": "data.store",
                              "artifact_path": str(store)}, "brg3")
    assert src == store                       # local mirror wins, as today
    assert seen["entity"] is False, "a local hit must not consult entity facts"


def test_run_resolvable_remote_raise_unchanged(monkeypatch):
    # The run-keyed remote raise (resolve_run_store miss + run_output_site
    # naming a remote site) keeps its "bring it home" shape — the bridge sits
    # BEHIND it, at the terminal error only.
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr("content.bio.project_locate.locate_project_files",
                        lambda name, limit=6: {"matches": []})
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_run_store",
                        lambda rid, name, **k: None)
    monkeypatch.setattr("content.bio.lifecycle.runs.run_output_site",
                        lambda rid, name: "siteB")
    try:
        p3._resolve_source({"run_id": "run_1", "name": "data.store"}, "brg4")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as ex:
        msg = str(ex)
    assert "lives on siteB" in msg and "bring it home" in msg, msg


# ── ref arm: registry variant + serving dispatch ────────────────────────────

def test_ref_arm_streams_through_data_read_range():
    # A ref-arm row serves through the FAKE data_read_range with the chunk rel
    # passed through as the tree MEMBER rel (NO base_rel join). Assembly + cache
    # hit short-circuit behave exactly as the run arm.
    pid = "s_ref"
    _register_ref(pid, "s-ref.store", ref="sha256:aa", site="siteA")
    payload = b"ref-arm-chunk-bytes"                 # > cap → multi-loop
    calls: list = []
    _orig = retention.data_read_range
    retention.data_read_range = _data_reader({"c/0.0": payload}, calls)
    try:
        out = rc.serve_remote_chunk(pid, "s-ref.store/c/0.0")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == payload
        assert len(calls) > 0, "MISS must exercise the ref backhaul (armed)"
        # rel passes through unchanged (no base_rel prefix), ref+site threaded
        assert all(c[1] == "c/0.0" for c in calls), calls
        assert all(c[0] == "sha256:aa" and c[2] == "siteA" for c in calls), calls
        n = len(calls)
        out2 = rc.serve_remote_chunk(pid, "s-ref.store/c/0.0")
        assert out2.status == "ok" and out2.path == out.path
        assert len(calls) == n, "cache HIT must NOT touch the backhaul"
    finally:
        retention.data_read_range = _orig


def test_run_arm_never_touches_data_read_range():
    # CEILING: a RUN-arm row still assembles via file_read_range and NEVER
    # dispatches data_read_range (the ref-arm sentinel raises if it does).
    pid = "s_runonly"
    _register(pid, "s-ro.store", base_rel="out/s.store", site="siteA")
    calls_run: list = []
    calls_data: list = []

    def _data_sentinel(*a, **k):
        calls_data.append((a, k))
        raise AssertionError("run arm must NOT call data_read_range")
    _orig_f = retention.file_read_range
    _orig_d = retention.data_read_range
    retention.file_read_range = _reader({"out/s.store/c/0.0": b"RUN-ARM"}, calls_run)
    retention.data_read_range = _data_sentinel
    try:
        out = rc.serve_remote_chunk(pid, "s-ro.store/c/0.0")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == b"RUN-ARM"
        assert calls_data == [], "data_read_range untouched by the run arm"
        assert len(calls_run) > 0
    finally:
        retention.file_read_range = _orig_f
        retention.data_read_range = _orig_d


def test_register_validation_exactly_one_arm():
    # Exactly one arm per row: both or neither is malformed → no row written
    # (lookup misses). Each single arm registers and looks up.
    pid = "s_val"
    rc.register_remote_store(pid, "both.store", site="siteA",
                             target="krn", base_rel="b", ref="sha256:x")
    assert rc.lookup_remote_store(pid, "both.store") is None, "both arms → ignored"
    rc.register_remote_store(pid, "none.store", site="siteA")
    assert rc.lookup_remote_store(pid, "none.store") is None, "no arm → ignored"
    rc.register_remote_store(pid, "run.store", site="siteA",
                             target="krn", base_rel="b")
    assert (rc.lookup_remote_store(pid, "run.store") or {}).get("target") == "krn"
    rc.register_remote_store(pid, "ref.store", site="siteA", ref="sha256:y")
    row = rc.lookup_remote_store(pid, "ref.store") or {}
    assert row.get("ref") == "sha256:y" and "target" not in row


def test_ref_arm_typed_errors_map_same():
    # data.missing → 404; task.invalid (e.g. rel-on-FILE misuse) → 404; any
    # other → 502 naming the site — identical mapping to the run arm.
    pid = "s_reftyped"
    _orig = retention.data_read_range
    _register_ref(pid, "rm.store", ref="sha256:m", site="siteQ")
    retention.data_read_range = _data_reader({}, [])         # any rel → data.missing
    try:
        out = rc.serve_remote_chunk(pid, "rm.store/gone")
        assert out.status == "missing" and out.http == 404
        cache = os.path.join(rc._cache_root(pid, "siteQ", "rm.store"), "gone")
        assert not os.path.exists(cache), "a missing ref chunk leaves NO cache file"
    finally:
        retention.data_read_range = _orig

    _register_ref(pid, "ti.store", ref="sha256:t", site="siteQ")

    def _ti(ref, rel=None, *, offset=0, length=None, site=None):
        raise ComputeError("task.invalid", "rel on file ref", stage="weft")
    retention.data_read_range = _ti
    try:
        out = rc.serve_remote_chunk(pid, "ti.store/x")
        assert out.status == "missing" and out.http == 404
    finally:
        retention.data_read_range = _orig

    _register_ref(pid, "er.store", ref="sha256:e", site="edgeX")

    def _er(ref, rel=None, *, offset=0, length=None, site=None):
        raise ComputeError("internal.error", "down", stage="weft", retryable=True)
    retention.data_read_range = _er
    try:
        out = rc.serve_remote_chunk(pid, "er.store/x")
        assert out.status == "error" and out.http == 502 and "edgeX" in out.detail
    finally:
        retention.data_read_range = _orig


# ── ref arm: launcher registration + per-verb degradation ────────────────────

def _by_ref_remote_entity(eid, *, ref, site="siteA",
                          path="/r/data.lstar.zarr", total_bytes=4096,
                          n_files=12):
    """A by-reference REMOTE dataset entity fixture (drives the REAL
    dataset_location + ref_stream_facts: home.site remote, by_reference True,
    metadata.ref, recorded dir shape via descriptor n_files). With ref=None
    this IS the mintable shape (durable home path recorded, ref lazily
    mintable — the path-lane registration, e.g. a producing-run-deleted
    remote store)."""
    return {"id": eid, "metadata": {
        "home": {"site": site, "path": path},
        "by_reference": True, "ref": ref,
        "descriptor": {"total_bytes": total_bytes, "n_files": n_files}}}


class _MintPort:
    """Fake data plane for the launch-time ref mint: records every sync_call;
    `data_register` answers `ref` (or raises when `fail`)."""
    def __init__(self, ref="sha256:minted", fail=False):
        self.calls: list = []
        self.ref, self.fail = ref, fail

    def sync_call(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if self.fail:
            raise RuntimeError("site unreachable")
        assert name == "data_register", name
        return {"ref": self.ref, "bytes": 4096, "files": 12}


def _patch_mint(monkeypatch, *, ref="sha256:minted", fail=False):
    """Wire the mint seam: fake compute port + a patch_metadata sentinel.
    Returns (port, writes) — `writes` records every patch_metadata call."""
    port = _MintPort(ref=ref, fail=fail)
    writes: list = []
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: port)
    monkeypatch.setattr("core.graph.entities.patch_metadata",
                        lambda eid, updates: writes.append((eid, updates)))
    return port, writes


def test_production_shape_ref_arm_end_to_end(monkeypatch, tmp_path):
    # THE production shape: entity-backed by-ref REMOTE store, run DEAD (the run
    # resolver is a sentinel that must NOT be consulted), metadata ref present,
    # ref verb live → launch() registers the ref arm and streams; the store
    # route then serves chunks via the FAKE ref backhaul.
    import content.bio.viewers.launchers.pagoda3 as p3
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("x")
    monkeypatch.setattr(p3, "pagoda3_dist_path", lambda: dist)
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: _by_ref_remote_entity(eid, ref="sha256:store-root"))
    seen = {"run_resolve": 0}

    def _run_sentinel(rid, name):
        seen["run_resolve"] += 1
        return {"target": "x", "site": "s", "store_rel": "r"}
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        _run_sentinel)
    # CEILING (the URL-lane shape): ref already recorded → the mint seam is
    # NEVER touched (no data_register, no metadata write).
    port, writes = _patch_mint(monkeypatch)
    pid = "prod_ref"
    node = {"entity_id": "ds_1", "name": "data.lstar.zarr",
            "artifact_path": "/r/data.lstar.zarr"}
    res = p3.launch(node, {"project_id": pid})
    assert res.store_path is None, "a streamed store has no local store_path"
    assert f"/pagoda3-store/{pid}/" in res.url
    assert seen["run_resolve"] == 0, "ref arm must engage WITHOUT the run resolve"
    assert port.calls == [] and writes == [], \
        "a recorded ref must never re-mint or rewrite metadata"
    store_key = res.url.split(f"/pagoda3-store/{pid}/", 1)[1].rstrip("/")
    row = rc.lookup_remote_store(pid, store_key)
    assert row and row.get("ref") == "sha256:store-root" and row.get("site") == "siteA"
    assert "target" not in row and "base_rel" not in row, row
    calls: list = []
    _orig = retention.data_read_range
    retention.data_read_range = _data_reader({"c/0.0": b"REF-STREAMED-CHUNK"}, calls)
    try:
        out = rc.serve_remote_chunk(pid, f"{store_key}/c/0.0")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == b"REF-STREAMED-CHUNK"
        assert calls and calls[0][1] == "c/0.0" and calls[0][2] == "siteA"
    finally:
        retention.data_read_range = _orig


def test_mint_failure_degrades_to_bridge_no_metadata_write(monkeypatch, tmp_path):
    # A MINTABLE by-ref REMOTE entity (ref:None, durable home recorded) whose
    # launch-time mint FAILS degrades to exactly today's path — the honesty
    # bridge (remote wording naming the home site) — with NO metadata write
    # (armed on the patch sentinel) and exactly ONE mint attempt. This is the
    # documented accepted divergence from the note's streaming promise.
    import content.bio.viewers.launchers.pagoda3 as p3
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("x")
    monkeypatch.setattr(p3, "pagoda3_dist_path", lambda: dist)
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: _by_ref_remote_entity(eid, ref=None))
    port, writes = _patch_mint(monkeypatch, fail=True)
    monkeypatch.setattr(p3, "_resolve_source",
                        lambda node, pid, sp=None: Path("/nonexistent/data.lstar.zarr"))
    try:
        p3.launch({"entity_id": "ds_1", "name": "data.lstar.zarr",
                   "artifact_path": "/r/data.lstar.zarr"}, {"project_id": "refnone1"})
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as ex:
        msg = str(ex)
    assert _REMOTEISH.search(msg) and "siteA" in msg, msg
    assert "source not found" not in msg, msg
    assert len(port.calls) == 1, "exactly ONE mint attempt per launch (armed)"
    assert port.calls[0][0] == "data_register"
    assert writes == [], "a failed mint must write NO metadata"


def test_mintable_end_to_end(monkeypatch, tmp_path):
    # THE closed-gap shape end-to-end: by-ref REMOTE store, run dead, ref
    # ABSENT but durable home recorded (the path-lane registration) → launch()
    # MINTS the ref (data_register(path, site=, ingest=False)), persists it
    # via patch_metadata (single key), registers the ref arm, and the store
    # route streams chunks via the fake ref backhaul.
    import content.bio.viewers.launchers.pagoda3 as p3
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("x")
    monkeypatch.setattr(p3, "pagoda3_dist_path", lambda: dist)
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: _by_ref_remote_entity(eid, ref=None))
    seen = {"run_resolve": 0}

    def _run_sentinel(rid, name):
        seen["run_resolve"] += 1
        return None
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        _run_sentinel)
    port, writes = _patch_mint(monkeypatch, ref="sha256:fresh-mint")
    pid = "mint_e2e"
    res = p3.launch({"entity_id": "ds_1", "name": "data.lstar.zarr",
                     "artifact_path": "/r/data.lstar.zarr"}, {"project_id": pid})
    # minted exactly as the eager registration lane does
    assert port.calls == [("data_register", ("/r/data.lstar.zarr",),
                           {"site": "siteA", "ingest": False})], port.calls
    # persisted race-safely: the single "ref" key, nothing else
    assert writes == [("ds_1", {"ref": "sha256:fresh-mint"})], writes
    assert seen["run_resolve"] == 0, "mintable arm must not consult the run resolver"
    store_key = res.url.split(f"/pagoda3-store/{pid}/", 1)[1].rstrip("/")
    row = rc.lookup_remote_store(pid, store_key)
    assert row and row.get("ref") == "sha256:fresh-mint" and row["site"] == "siteA"
    calls: list = []
    _orig = retention.data_read_range
    retention.data_read_range = _data_reader({"c/0.0": b"MINTED-CHUNK"}, calls)
    try:
        out = rc.serve_remote_chunk(pid, f"{store_key}/c/0.0")
        assert out.status == "ok"
        assert open(out.path, "rb").read() == b"MINTED-CHUNK"
        assert calls and calls[0][0] == "sha256:fresh-mint"
    finally:
        retention.data_read_range = _orig


def test_unmintable_shapes_no_mint_attempt(monkeypatch):
    # Non-mintable shapes are UNCHANGED: ref None with NO recorded home path /
    # ref_path (nothing to mint from), and a local by-ref path — neither arm
    # registers and the data plane is NEVER touched (armed sentinel).
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    port, writes = _patch_mint(monkeypatch)
    # (a) remote by-ref, ref None, home has SITE only — no path anywhere
    e_nopath = {"id": "ds_1", "metadata": {
        "home": {"site": "siteA"}, "by_reference": True, "ref": None,
        "descriptor": {"total_bytes": 4096, "n_files": 12}}}
    # (b) local by-ref with a recorded path (mint would be pointless — local
    #     bytes resolve through today's local tiers)
    e_local = {"id": "ds_2", "metadata": {
        "by_reference": True, "ref": None, "ref_path": "/l/data.lstar.zarr",
        "descriptor": {"total_bytes": 4096, "n_files": 12}}}
    for e in (e_nopath, e_local):
        assert p3.ref_stream_facts(e, "data.lstar.zarr") is None, e
        monkeypatch.setattr("core.graph.entities.get_entity",
                            lambda eid, _e=e: _e)
        assert p3._register_remote_stream(
            {"entity_id": e["id"], "name": "data.lstar.zarr",
             "artifact_path": "/r/data.lstar.zarr"}, "unmint1") is None
    assert port.calls == [], "unmintable shapes must never touch the data plane"
    assert writes == []


def test_per_verb_matrix_ref_absent_run_present(monkeypatch):
    # ref verb ABSENT + run verb PRESENT: a by-ref entity with a ref cannot use
    # the ref arm (data_read_range must NEVER be dispatched — armed sentinel) and
    # with no producing run it registers nothing; a RUN-arm store still streams.
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention._RANGE_VERB)

    def _data_sentinel(*a, **k):
        raise AssertionError("data_read_range dispatched with ref verb absent")
    monkeypatch.setattr("core.compute.retention.data_read_range", _data_sentinel)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: _by_ref_remote_entity(eid, ref="sha256:x"))
    monkeypatch.setattr("content.bio.lifecycle.runs.run_id_for_entity",
                        lambda eid: None)               # no producing run
    key = p3._register_remote_stream(
        {"entity_id": "ds_1", "name": "data.lstar.zarr",
         "artifact_path": "/r/data.lstar.zarr"}, "matrix1")
    assert key is None, "ref verb absent + no run → neither arm registers"
    # the run arm is unaffected — a run-keyed store still streams
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        lambda rid, name: {"target": "krn_z", "site": "siteZ",
                                           "store_rel": "out/x.store",
                                           "size": 9, "digest": "d"})
    key2 = p3._register_remote_stream(
        {"run_id": "run_9", "name": "x.store", "artifact_path": "work/x.store"},
        "matrix1")
    assert key2 and key2.startswith("x-")


def test_per_verb_matrix_both_absent_by_ref(monkeypatch):
    # BOTH verbs absent → today's full degradation: neither arm dispatches, and
    # the run resolver is not even consulted (armed).
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr("core.compute.retention.range_read_available",
                        lambda verb=retention._RANGE_VERB: False)

    def _data_sentinel(*a, **k):
        raise AssertionError("data_read_range dispatched with verbs absent")
    monkeypatch.setattr("core.compute.retention.data_read_range", _data_sentinel)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: _by_ref_remote_entity(eid, ref="sha256:x"))
    seen = {"run": False}

    def _run_sentinel(rid, name):
        seen["run"] = True
        return None
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        _run_sentinel)
    key = p3._register_remote_stream(
        {"entity_id": "ds_1", "run_id": "run_x", "name": "data.lstar.zarr",
         "artifact_path": "/r/data.lstar.zarr"}, "matrix2")
    assert key is None
    assert seen["run"] is False, "run verb absent must short-circuit before resolving"


def test_ref_arm_requires_by_reference_remote_and_store_suffix(monkeypatch):
    # Ceilings on the ref-arm gate: a LOCAL by-ref dataset (home unset ⇒ local),
    # and a non-store name (.h5ad — a FILE that would need conversion), both fall
    # through the ref arm even with a ref + the verb live.
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        lambda rid, name: None)         # run arm can't help either
    monkeypatch.setattr("content.bio.lifecycle.runs.run_id_for_entity", lambda eid: None)
    # LOCAL by-ref (home site unset) — not remote → no ref arm
    monkeypatch.setattr("core.graph.entities.get_entity", lambda eid: {
        "id": eid, "metadata": {"by_reference": True, "ref": "sha256:x"}})
    assert p3._register_remote_stream(
        {"entity_id": "ds_1", "name": "data.lstar.zarr",
         "artifact_path": "data.lstar.zarr"}, "gate1") is None
    # remote by-ref with a ref, but a FILE name (.h5ad) — not a dir store
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: _by_ref_remote_entity(
                            eid, ref="sha256:x", path="/r/data.h5ad"))
    assert p3._register_remote_stream(
        {"entity_id": "ds_1", "name": "data.h5ad",
         "artifact_path": "/r/data.h5ad"}, "gate2") is None


def test_entity_note_ref_arm_streams_no_round_trip(monkeypatch):
    # Pre-flight: a by-ref remote store with a ref + the ref verb promises
    # streaming from RECORDED FACTS ALONE — the run resolver (armed sentinel) is
    # NEVER consulted, and the over-gate refuse wording does not appear.
    from content.bio.tools.viewers import _entity_location_note
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    seen = {"resolve": 0}

    def _sentinel(rid, name):
        seen["resolve"] += 1
        return {"target": "x", "site": "s", "store_rel": "r"}
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        _sentinel)
    monkeypatch.setattr("content.bio.lifecycle.runs.run_id_for_entity",
                        lambda eid: "run_x")
    e = _by_ref_remote_entity("ent_1", ref="sha256:root",
                              path="/remote/siteA/data.lstar.zarr",
                              total_bytes=3 * 1024**3)     # over-gate size
    e["artifact_path"] = "/remote/siteA/data.lstar.zarr"
    note = _entity_location_note(e)
    assert note and "stream on demand" in note.lower(), note
    assert "OVER the transfer gate" not in note and "refuse" not in note, note
    assert "mirror the dataset locally" in note
    assert seen["resolve"] == 0, "ref arm must NOT consult the run resolver"


def test_ref_arm_refuses_file_shaped_or_unknown_payload(monkeypatch):
    # R2: the recorded payload shape must CONFIRM a directory tree
    # (descriptor/fingerprint n_files >= 2 — a single FILE fingerprints as
    # n_files=1). A FILE-shaped ref wearing the store suffix, and a
    # descriptor-less registration, both REFUSE to the materialize path:
    # admission would mean a dead viewer (every chunk a mute 404 — the
    # substrate refuses rel-on-FILE), refusal costs one gated fetch that
    # handles both shapes. A fingerprint-only dir shape (no descriptor) still
    # ADMITS — either recorded source confirms.
    import content.bio.viewers.launchers.pagoda3 as p3
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    node = {"entity_id": "ds_1", "name": "data.lstar.zarr",
            "artifact_path": "/r/data.lstar.zarr"}
    # FILE-shaped: n_files == 1
    e_file = _by_ref_remote_entity("ds_1", ref="sha256:x", n_files=1)
    # shape unrecorded: no n_files anywhere
    e_nodesc = _by_ref_remote_entity("ds_1", ref="sha256:y")
    e_nodesc["metadata"]["descriptor"] = {"total_bytes": 4096}
    for e in (e_file, e_nodesc):
        monkeypatch.setattr("core.graph.entities.get_entity",
                            lambda eid, _e=e: _e)
        assert p3._register_remote_stream(node, "shape1") is None, e["metadata"]
        assert p3.ref_stream_facts(e, "data.lstar.zarr") is None
    # fingerprint-only dir confirmation (descriptor absent) → admits
    e_fp = _by_ref_remote_entity("ds_1", ref="sha256:z")
    e_fp["metadata"]["descriptor"] = {}
    e_fp["metadata"]["fingerprint"] = {"exists": True, "n_files": 9,
                                       "digest": "fp1"}
    facts = p3.ref_stream_facts(e_fp, "data.lstar.zarr")
    assert facts and facts["ref"] == "sha256:z" and facts["digest"] == "fp1"


def test_misshaped_file_ref_every_chunk_404s():
    # R2 downstream surface: if a FILE-shaped ref ever reaches the registry
    # anyway (a mis-registration past the recorded-shape gate — e.g. facts
    # recorded wrong), the substrate refuses rel-on-FILE with typed
    # task.invalid on EVERY member rel. The route must answer 404 for each
    # (mute but honest — never a 500/502 storm) and cache NOTHING.
    pid = "s_fileref"
    _register_ref(pid, "fr.store", ref="sha256:file-not-tree", site="siteF")
    calls: list = []

    def _file_ref(ref, rel=None, *, offset=0, length=None, site=None):
        calls.append(rel)
        raise ComputeError("task.invalid", "rel on a FILE ref", stage="weft")
    _orig = retention.data_read_range
    retention.data_read_range = _file_ref
    try:
        for rel in (".zattrs", "c/0.0", "meta.json"):
            out = rc.serve_remote_chunk(pid, f"fr.store/{rel}")
            assert out.status == "missing" and out.http == 404, (rel, out)
        assert len(calls) == 3, "every rel must have hit the backhaul (armed)"
        root = rc._cache_root(pid, "siteF", "fr.store")
        leftovers = [fn for _dp, _d, fns in os.walk(root) for fn in fns]
        assert leftovers == [], f"a refused ref must cache NOTHING: {leftovers}"
    finally:
        retention.data_read_range = _orig


def test_note_launcher_ref_agreement_matrix(monkeypatch):
    # R3: for EVERY entity shape, the NOTE's stream verdict must EQUAL the
    # LAUNCHER's register verdict (both run-arm paths disabled, so only the
    # ref arm answers) — a gate added to one side but not the other fails
    # here. The launcher's mint seam is faked SUCCESSFUL, so the comparison is
    # promise-vs-happy-path (the one accepted divergence — mint FAILURE at
    # click — is guarded separately). Armed: the two streaming shapes must
    # verdict True (a matrix where everything declines measured nothing), and
    # ONLY those (ceiling); the mint fires for exactly the mintable shape.
    import content.bio.viewers.launchers.pagoda3 as p3
    from content.bio.tools import viewers as tv
    monkeypatch.setattr(
        "core.compute.retention.range_read_available",
        lambda verb=retention._RANGE_VERB: verb == retention.DATA_RANGE_VERB)
    monkeypatch.setattr("content.bio.lifecycle.runs.resolve_remote_store_stream",
                        lambda rid, name: None)
    monkeypatch.setattr("content.bio.lifecycle.runs.run_id_for_entity",
                        lambda eid: None)
    port, writes = _patch_mint(monkeypatch, ref="sha256:matrix-mint")
    e_not_by_ref = _by_ref_remote_entity("e3", ref="sha256:c")
    e_not_by_ref["metadata"]["by_reference"] = False
    e_local = {"id": "e5", "metadata": {
        "by_reference": True, "ref": "sha256:e",
        "descriptor": {"total_bytes": 4096, "n_files": 9}}}
    e_unmintable = {"id": "e7", "metadata": {
        "home": {"site": "siteA"}, "by_reference": True, "ref": None,
        "descriptor": {"total_bytes": 4096, "n_files": 9}}}
    shapes = {
        "store_by_ref_remote": (_by_ref_remote_entity("e1", ref="sha256:a"),
                                "data.lstar.zarr"),
        "mintable": (_by_ref_remote_entity("e8", ref=None), "data.lstar.zarr"),
        "file_by_ref_remote": (_by_ref_remote_entity("e2", ref="sha256:b",
                                                     path="/r/data.h5ad"),
                               "data.h5ad"),
        "not_by_reference": (e_not_by_ref, "data.lstar.zarr"),
        "ref_none_unmintable": (e_unmintable, "data.lstar.zarr"),
        "local": (e_local, "data.lstar.zarr"),
        "file_shaped_ref": (_by_ref_remote_entity("e6", ref="sha256:f",
                                                  n_files=1),
                            "data.lstar.zarr"),
    }
    verdicts = {}
    for label, (e, name) in shapes.items():
        monkeypatch.setattr("core.graph.entities.get_entity",
                            lambda eid, _e=e: _e)
        note_v = tv._remote_stream_ready(None, name, entity=e)
        launch_v = p3._register_remote_stream(
            {"entity_id": e["id"], "name": name,
             "artifact_path": f"/r/{name}"}, f"agree_{label}") is not None
        assert note_v == launch_v, \
            f"{label}: note says {note_v}, launcher says {launch_v} — DRIFT"
        verdicts[label] = note_v
    assert verdicts["store_by_ref_remote"] is True, "matrix measured nothing"
    assert verdicts["mintable"] is True, "the mintable shape must stream"
    assert sum(verdicts.values()) == 2, verdicts   # ceiling: exactly two stream
    # the mint fired for exactly the mintable shape, nothing else
    assert len(port.calls) == 1 and port.calls[0][0] == "data_register"
    assert writes == [("e8", {"ref": "sha256:matrix-mint"})], writes


_TESTS = [
    test_safe_rel_accepts_normal_chunk,
    test_safe_rel_rejects_traversal_absolute_empty,
    test_registry_roundtrip_and_persists_to_disk,
    test_registry_miss_returns_none,
    test_serve_unregistered_returns_none,
    test_serve_traversal_rejected_before_backhaul,
    test_cache_miss_backhauls_then_hit_short_circuits,
    test_capped_multiloop_assembly,
    test_zero_length_chunk_file,
    test_data_missing_returns_404_no_cache_file,
    test_data_missing_midstream_no_partial_cache,
    test_task_invalid_returns_404,
    test_backhaul_error_returns_502_naming_site,
    test_verb_absent_midsession_502,
    test_digest_change_wipes_cache_same_digest_keeps,
    test_digest_change_wipes_prev_site_cache,
    test_numeric_sibling_guesses,
    test_miss_serves_target_singular_then_one_inline_prefetch,
    test_prefetch_abandons_not_read_and_error_entries,
    test_batch_unsupported_flips_to_singular_once,
    test_retryable_internal_error_retries_once,
    test_ref_arm_outcomes_carry_immutable_run_arm_not,
    test_backhaul_emits_console_events,
    test_prefetch_batch_emits_one_summary_event,
    test_route_cache_headers_by_mutability,
    test_lru_sweep_evicts_oldest,
    test_sweep_never_evicts_the_just_installed_file,
    test_lookup_does_not_create_registry_dir,
    test_aborted_assembly_leaves_no_partial_temp,
    test_route_local_branch_byte_identical_and_skips_registry,
    test_route_registry_miss_is_today_404,
    test_route_traversal_403_before_registry,
    test_route_remote_hit_serves_cached_chunk,
    test_route_remote_missing_is_404,
    test_route_backhaul_error_is_502,
    test_resolve_remote_store_stream_gate_aligned_rel,
    test_resolve_remote_store_stream_full_rel_name,
    test_resolve_remote_store_stream_interior_only_is_none,
    test_resolve_remote_store_stream_local_and_file_are_none,
    test_resolve_remote_store_stream_missing_run_is_none,
    test_entity_note_streams_when_available,
    test_entity_note_fetch_wording_when_verb_absent,
    test_note_helpers_called_only_from_shared_decision,
    test_register_remote_stream_degrades_when_verb_absent,
    test_register_remote_stream_registers_and_returns_key,
    test_graft_dir_folders_carry_their_disk_path,
    test_shadow_mirror_resolves_local_serve,
    test_shadow_no_mirror_falls_through_to_streaming,
    test_unrelated_tree_nodes_unchanged,
    test_launch_terminal_error_names_home_site_for_by_ref_remote,
    test_launch_terminal_error_generic_shape_ceilings,
    test_by_ref_remote_with_local_mirror_resolves_locally,
    test_run_resolvable_remote_raise_unchanged,
    test_ref_arm_streams_through_data_read_range,
    test_run_arm_never_touches_data_read_range,
    test_register_validation_exactly_one_arm,
    test_ref_arm_typed_errors_map_same,
    test_production_shape_ref_arm_end_to_end,
    test_mint_failure_degrades_to_bridge_no_metadata_write,
    test_mintable_end_to_end,
    test_unmintable_shapes_no_mint_attempt,
    test_per_verb_matrix_ref_absent_run_present,
    test_per_verb_matrix_both_absent_by_ref,
    test_ref_arm_requires_by_reference_remote_and_store_suffix,
    test_entity_note_ref_arm_streams_no_round_trip,
    test_ref_arm_refuses_file_shaped_or_unknown_payload,
    test_misshaped_file_ref_every_chunk_404s,
    test_note_launcher_ref_agreement_matrix,
]


class _MP:
    """Minimal monkeypatch for the standalone runner (string 'module.attr'
    targets, auto-undone), mirroring tests/test_viewer_link_resolution.py."""
    def __init__(self):
        self._undo = []

    def setattr(self, *args):
        # Support both pytest forms: setattr("mod.attr", value) and
        # setattr(obj, "attr", value).
        if len(args) == 2:
            import importlib
            target, value = args
            mod_name, attr = target.rsplit(".", 1)
            obj = importlib.import_module(mod_name)
        else:
            obj, attr, value = args
        self._undo.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, value)

    def undo(self):
        for mod, attr, old in reversed(self._undo):
            setattr(mod, attr, old)
        self._undo.clear()


def _standalone() -> int:
    import inspect
    import traceback
    rc_code = 0
    for t in _TESTS:
        mp = _MP()
        try:
            params = inspect.signature(t).parameters
            kw = {}
            if "monkeypatch" in params:
                kw["monkeypatch"] = mp
            if "tmp_path" in params:
                kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_range_tp_"))
            t(**kw)
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc_code = 1
        finally:
            mp.undo()
    return rc_code


if __name__ == "__main__":
    raise SystemExit(_standalone())
