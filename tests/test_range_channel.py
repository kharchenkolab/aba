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
              site="siteA", size=100, digest="d1"):
    rc.register_remote_store(pid, store_key, target=target, base_rel=base_rel,
                             site=site, size=size, digest=digest)


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
    test_launch_terminal_error_names_home_site_for_by_ref_remote,
    test_launch_terminal_error_generic_shape_ceilings,
    test_by_ref_remote_with_local_mirror_resolves_locally,
    test_run_resolvable_remote_raise_unchanged,
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
