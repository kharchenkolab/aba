"""Remote-output resolution lifecycle (Layer 0 + Layer 1): a Run output produced on
a NON-local site is resolvable / servable / listable from the controller.

Everything runs against a temp entity DB with `core.compute.retention` (and the
compute adapter) monkeypatched — no real substrate. Guards the invariant's core:
ONE canonical resolver (`locate_run_output`, which never transfers) + ONE mover
(`materialize_run_output`, budgeted by the ACTION surface), fetched-copy caching
only against a freshness digest (an open run's still-growing output re-fetches;
a same-size rewrite invalidates via mtime), atomic installs that never destroy a
current copy, presentation parity (captured truth appears on the surface the
user reads, with honest "on <site>" refusals past a gate), and the full
produce-remotely → open-here → settle lifecycle.

Run: python tests/test_remote_output_resolution.py  (also pytest-collectable)
"""
from __future__ import annotations
import base64
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_remoteout_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "r.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402  (loads the app + type registry)
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from content.bio.lifecycle import runs as runs_mod  # noqa: E402
from content.bio.web.routes import runs as rt  # noqa: E402
import core.compute.retention as retmod  # noqa: E402
import core.compute.adapter as _admod  # noqa: E402

init_db()

try:  # pytest full-suite isolation — no-op under the standalone runner
    import pytest

    @pytest.fixture(autouse=True)
    def _no_leaked_substrate(monkeypatch):
        """These tests mock the substrate at the retention/datasets seam and
        rely on `get_compute()` raising (substrate offline) for the live-first
        branches. Under the FULL pytest sweep an earlier module can leave a
        fake global adapter installed, which then ANSWERS those calls and
        shortcuts the mocked fallbacks — force the offline path per test."""
        monkeypatch.setattr(_admod, "_adapter", None, raising=False)
        yield
except ImportError:  # pragma: no cover
    pass


def _mk_run(**md) -> str:
    out = create_entity(entity_type="analysis", title="Remote Run", metadata=md)
    return out if isinstance(out, str) else out["id"]


# ── 1. remote tier: fetch once, cache, reuse ──────────────────────────────────

def test_resolve_run_file_remote_tier_fetches_then_caches(monkeypatch):
    payload = b"hello-remote-output"
    rid = _mk_run(thread_id="t", run_state="open", weft_targets=["krn_remote"])
    rel = "out.h5ad"
    row = {"state": "done", "target": "krn_remote", "site": "mendel",
           "location": "/remote/keep/dir", "in_place": True}
    reads = {"n": 0}
    monkeypatch.setattr(retmod, "retained", lambda **kw: [row])
    monkeypatch.setattr(retmod, "file_stat",
                        lambda t, r: {"exists": True, "bytes": len(payload)})
    # pin the live-first stat seam too — a leaked global substrate stub from an
    # earlier full-suite module must not answer ahead of the retention mock
    monkeypatch.setattr(runs_mod, "_live_file_stat",
                        lambda t, r: {"exists": True, "bytes": len(payload)})

    def _read(t, r, max_bytes=1 << 20):
        reads["n"] += 1
        return {"bytes_b64": base64.b64encode(payload).decode(), "truncated": False}
    monkeypatch.setattr(retmod, "file_read", _read)

    p1 = runs_mod.resolve_run_file(rid, rel)
    assert p1 and os.path.isfile(p1)
    assert Path(p1).read_bytes() == payload
    assert reads["n"] == 1
    # second open = cache hit, no further transport
    p2 = runs_mod.resolve_run_file(rid, rel)
    assert p2 == p1
    assert reads["n"] == 1


# ── 2. oversize remote → None + informative 413 naming the site ───────────────

def test_oversize_remote_file_route_names_site(monkeypatch):
    from fastapi import HTTPException
    big = 200 * 1024 * 1024
    rid = _mk_run(thread_id="t", run_state="open", weft_targets=["krn_remote"])
    rel = "huge.zarr.bin"
    row = {"state": "done", "target": "krn_remote", "site": "mendel",
           "location": "/remote/keep/dir", "in_place": True}
    monkeypatch.setattr(retmod, "retained", lambda **kw: [row])
    monkeypatch.setattr(retmod, "file_stat",
                        lambda t, r: {"exists": True, "bytes": big})
    monkeypatch.setattr(runs_mod, "_live_file_stat",
                        lambda t, r: {"exists": True, "bytes": big})
    # a preview read of a too-big file comes back truncated
    monkeypatch.setattr(retmod, "file_read",
                        lambda t, r, max_bytes=1 << 20: {
                            "bytes_b64": base64.b64encode(b"x" * 16).decode(),
                            "truncated": True, "bytes_total": big})

    # transparent resolve declines an oversize remote file
    assert runs_mod.resolve_run_file(rid, rel) is None
    # the /file route turns that into a site-named 413
    try:
        rt.run_file(rid, rel)
        raise AssertionError("expected HTTPException")
    except HTTPException as e:
        assert e.status_code == 413
        assert "mendel" in e.detail
        assert "bring it home" in e.detail.lower()


# ── 3. local fast path: no transport ──────────────────────────────────────────

def test_local_fast_path_no_transport(monkeypatch):
    local_dir = tempfile.mkdtemp(prefix="aba_localkeep_", dir=_RT)
    rel = "fig.png"
    (Path(local_dir) / rel).write_bytes(b"png-bytes")
    rid = _mk_run(thread_id="t", run_state="open", weft_targets=["krn_x"])
    row = {"state": "done", "target": "krn_x", "site": "local",
           "location": local_dir, "in_place": False}
    monkeypatch.setattr(retmod, "retained", lambda **kw: [row])
    calls = {"n": 0}
    monkeypatch.setattr(retmod, "file_stat",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {})
    monkeypatch.setattr(retmod, "file_read",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {})
    monkeypatch.setattr(retmod, "retain",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or {})

    p = runs_mod.resolve_run_file(rid, rel)
    assert p == str(Path(local_dir).resolve() / rel)
    assert calls["n"] == 0        # local tier never touched the substrate


# ── 4. produced[] entries carry the step's site ───────────────────────────────

def test_produced_entries_carry_remote_site():
    from content.bio.tools.run_exec import _write_exec_record
    from core.graph import exec_records as er

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""
        timed_out = False
        cancelled = False

    class _Sess:
        site = "mendel"
        kernel_id = "krn_remote"

        def execute(self, *a, **k):
            return _Res()

    eid = _write_exec_record(
        lang="python", ctx={"thread_id": "t"}, code="print(1)", cwd=_RT,
        sess=_Sess(), started_iso="2026-07-16T00:00:00+00:00", started_ts=0.0,
        res=_Res(), plots=[], tables=[],
        files=[{"url": "/artifacts/x", "bytes": 10, "original_name": "a.h5ad"}])
    assert eid
    rec = er.get(eid)
    produced = rec.get("produced") or []
    assert produced and all(p.get("site") == "mendel" for p in produced)
    # and the run/exec compute block still records the site (Layer-0 source)
    assert rec.get("compute", {}).get("site") == "mendel"


def test_produced_entries_omit_site_when_local():
    from content.bio.tools.run_exec import _write_exec_record
    from core.graph import exec_records as er

    class _Res:
        returncode = 0
        stdout = ""
        stderr = ""
        timed_out = False
        cancelled = False

    class _Sess:                      # no site attr → local
        def execute(self, *a, **k):
            return _Res()

    eid = _write_exec_record(
        lang="python", ctx={"thread_id": "t"}, code="print(1)", cwd=_RT,
        sess=_Sess(), started_iso="2026-07-16T00:00:00+00:00", started_ts=0.0,
        res=_Res(), plots=[], tables=[],
        files=[{"url": "/artifacts/x", "bytes": 10, "original_name": "a.h5ad"}])
    rec = er.get(eid)
    assert all("site" not in p for p in (rec.get("produced") or []))


# ── 5. tree: by-reference remote dataset → real filenames, not a .bin ─────────

def test_remote_dataset_tree_lists_real_files():
    from content.bio.files.tree import build_files_tree
    top = ["s.barcodes.tsv.gz", "s.features.tsv.gz", "s.matrix.mtx.gz"]
    create_entity(
        entity_type="dataset",
        title="GSM5746259 10x counts covid pbmc day 0 pt 145",
        artifact_path="/home/pkharchenko/aba_data/GSE192391/GSM5746259",
        metadata={
            "by_reference": True,
            "home": {"site": "mendel",
                     "path": "/home/pkharchenko/aba_data/GSE192391/GSM5746259"},
            "descriptor": {"top": top, "n_files": 3, "total_bytes": 47800000,
                           "truncated": False},
            "fingerprint": {"top": top, "n_files": 3},
        })
    tree = build_files_tree(include_archived=False)

    def _walk(n):
        yield n
        for c in n.get("children") or []:
            yield from _walk(c)

    ds_folders = [n for n in _walk(tree)
                  if n.get("entity_type") == "dataset" and n.get("kind") == "folder"]
    assert ds_folders, "remote dataset should render as a folder"
    ds = ds_folders[0]
    child_names = {c["name"] for c in ds.get("children") or []}
    assert child_names == set(top)
    assert not any(n.get("name", "").endswith(".bin") for n in _walk(tree))
    assert ds.get("site") == "mendel"
    assert all(c.get("site") == "mendel" for c in ds["children"])
    assert all(c.get("size") is None for c in ds["children"])   # no fabricated sizes


# ── 6. pagoda3 _resolve_source routes through the canonical resolver ──────────

def test_pagoda3_resolve_source_uses_canonical(monkeypatch):
    from content.bio.viewers.launchers import pagoda3
    fetched = tempfile.mkdtemp(prefix="aba_store_", dir=_RT)
    node = {"run_id": "ana_x", "artifact_path": "processed.lstar.zarr",
            "name": "processed.lstar.zarr"}
    monkeypatch.setattr(runs_mod, "resolve_run_store", lambda rid, name, **k: fetched)
    got = pagoda3._resolve_source(node, "default")
    assert str(got) == fetched


def test_pagoda3_resolve_source_names_site_when_unresolvable(monkeypatch):
    from content.bio.viewers.launchers import pagoda3
    node = {"run_id": "ana_x", "artifact_path": "processed.lstar.zarr",
            "name": "processed.lstar.zarr"}
    monkeypatch.setattr(runs_mod, "resolve_run_store", lambda rid, name, **k: None)
    monkeypatch.setattr(runs_mod, "run_output_site", lambda rid, name: "mendel")
    try:
        pagoda3._resolve_source(node, "default")
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError as e:
        assert "mendel" in str(e)
        assert "bring it home" in str(e).lower()


def _stable_inv(entries):
    """A minimal run_inventory-shaped result for the store members `entries`
    (each `{path, bytes}`) — mocked in place of the live substrate inventory."""
    return {"entries": list(entries)}


# ── 7. live-kernel DIRECTORY store: data-plane bring-back, revalidated cache ───

def test_live_kernel_store_brings_back_via_data_plane(monkeypatch):
    """An OPEN run's DIRECTORY store lives in a live kernel sandbox: retain there
    is pinned-pending and the read channel caps at 8 MB, so the resolver must
    bring it home over the datasets data-plane on the sandbox abs path
    (register_source -> fetch). Confirms: kernel target -> data-plane fetch of
    `<root>/kernels/<kid>/<name>`, atomic into the run cache, and — because a live
    store is MUTABLE — a cache hit on repeat ONLY when the inventory is unchanged."""
    rid = _mk_run(weft_targets=["krn_live1"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_live1": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(runs_mod, "resolve_run_output_path", lambda r, n: None)
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {})   # not a single file
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    # a stable live inventory → the fetched copy revalidates as current
    inv = _stable_inv([{"path": "store.zarr/zarr.json", "bytes": 2},
                       {"path": "store.zarr/chunks/0", "bytes": 100}])
    monkeypatch.setattr(runs_mod, "_live_inventory", lambda t, **kw: inv)

    import core.data.datasets as dsmod
    calls = {"fetches": 0, "abs": None}

    def _reg(source, *, site=None, **kw):
        calls["abs"] = (source, site)
        return {"home": {"site": site, "path": source}}

    def _fetch(meta, to_path, **kw):
        calls["fetches"] += 1
        os.makedirs(to_path, exist_ok=True)
        open(os.path.join(to_path, "zarr.json"), "w").write("{}")
        os.makedirs(os.path.join(to_path, "chunks"), exist_ok=True)
        open(os.path.join(to_path, "chunks", "0"), "wb").write(b"x" * 100)
        return {"ok": True, "path": to_path}

    monkeypatch.setattr(dsmod, "register_source", _reg)
    monkeypatch.setattr(dsmod, "fetch", _fetch)

    p1 = runs_mod.resolve_run_store(rid, "store.zarr")
    assert p1 and os.path.isdir(p1) and os.path.isfile(os.path.join(p1, "zarr.json"))
    assert calls["abs"][0].endswith("kernels/krn_live1/store.zarr")
    assert calls["fetches"] == 1
    # cache hit — inventory unchanged, so no second fetch
    p2 = runs_mod.resolve_run_store(rid, "store.zarr")
    assert p2 == p1 and calls["fetches"] == 1


# ── 8. mid-band (8–50 MB) single file on a LIVE kernel → data-plane, not a 413 ─

def test_mid_band_single_file_live_kernel_uses_data_plane(monkeypatch):
    """A remote single FILE in (8 MB, 50 MB] whose target is a LIVE kernel: the
    retain lane defers, so the resolver must route it through the data-plane and
    materialize a local copy — NOT return None (which the /file route would mis-
    report as 'too large'). RED on HEAD (mid-band took the deferring retain lane)."""
    size = 20 * 1024 * 1024
    rid = _mk_run(weft_targets=["krn_mid"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_mid": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs_mod, "_live_file_stat",
                        lambda t, r: {"exists": True, "bytes": size})
    # retain must NOT be the lane here — fail loudly if the mid-band touches it
    monkeypatch.setattr(retmod, "retain",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("mid-band kernel file must not use retain")))

    import core.data.datasets as dsmod
    calls = {"n": 0, "abs": None}

    def _reg(source, *, site=None, **kw):
        calls["abs"] = source
        return {"home": {"site": site, "path": source}}

    def _fetch(meta, to_path, **kw):
        calls["n"] += 1
        os.makedirs(os.path.dirname(to_path) or ".", exist_ok=True)
        open(to_path, "wb").write(b"y" * 1024)
        return {"ok": True, "path": to_path}

    monkeypatch.setattr(dsmod, "register_source", _reg)
    monkeypatch.setattr(dsmod, "fetch", _fetch)

    p = runs_mod.resolve_run_file(rid, "big.csv")
    assert p and os.path.isfile(p)
    assert calls["n"] == 1
    assert calls["abs"].endswith("kernels/krn_mid/big.csv")


# ── 9. unknown remote size ⇒ declined (no ungated fetch); route names the site ─

def test_unknown_size_declines_and_route_names_site(monkeypatch):
    from fastapi import HTTPException
    rid = _mk_run(weft_targets=["krn_u"], run_state="open")
    row = {"state": "done", "target": "krn_u", "site": "mendel",
           "location": "/remote/keep/dir", "in_place": True}
    monkeypatch.setattr(retmod, "retained", lambda **kw: [row])
    # file_stat reports existence but NO byte count (size unknown) — pinned on
    # BOTH seams so a leaked substrate stub can't answer the live-first branch
    monkeypatch.setattr(retmod, "file_stat", lambda t, r: {"exists": True})
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {"exists": True})
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_u": "mendel"})
    touched = {"read": 0, "retain": 0}
    monkeypatch.setattr(retmod, "retain",
                        lambda *a, **k: touched.__setitem__("retain", touched["retain"] + 1) or {})

    # unknown size ⇒ transparent resolve declines, WITHOUT an ungated fetch
    assert runs_mod.resolve_run_file(rid, "mystery.bin") is None
    assert touched["retain"] == 0

    def _read(t, r, max_bytes=1 << 20):
        touched["read"] += 1
        return {"bytes_b64": base64.b64encode(b"x" * 16).decode(),
                "truncated": True, "bytes_total": 0}
    monkeypatch.setattr(retmod, "file_read", _read)
    try:
        rt.run_file(rid, "mystery.bin")
        raise AssertionError("expected HTTPException")
    except HTTPException as e:
        assert e.status_code == 413 and "mendel" in e.detail


# ── 10. traversal rejected at BOTH the file lane and the store lane ────────────

def test_traversal_rejected_file_and_store(monkeypatch):
    rid = _mk_run(weft_targets=["krn_t"], run_state="open")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_t": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(runs_mod, "resolve_run_output_path", lambda r, n: None)
    # a stat that WOULD confirm the escaping name (a hostile/loose substrate)
    monkeypatch.setattr(runs_mod, "_live_file_stat",
                        lambda t, r: {"exists": True, "bytes": 128})
    monkeypatch.setattr(runs_mod, "_live_inventory",
                        lambda t, **kw: _stable_inv([{"path": "../../escape/x", "bytes": 1}]))
    reads = {"n": 0}
    monkeypatch.setattr(retmod, "file_read",
                        lambda *a, **k: reads.__setitem__("n", reads["n"] + 1) or {})

    import core.data.datasets as dsmod
    fetches = {"n": 0}
    monkeypatch.setattr(dsmod, "register_source",
                        lambda s, **k: fetches.__setitem__("n", fetches["n"] + 1) or {})
    monkeypatch.setattr(dsmod, "fetch",
                        lambda *a, **k: fetches.__setitem__("n", fetches["n"] + 1) or {"ok": False})

    # file lane: an escaping rel is refused before any read/fetch/cache touch
    assert runs_mod.resolve_run_file(rid, "../../escape.txt") is None
    assert reads["n"] == 0 and fetches["n"] == 0
    # store lane: an escaping name is refused before the kernel abs path is used
    esc = {"run_id": rid, "rel": "../../escape", "kind": "dir",
           "locality": "remote", "site": "mendel", "target": "krn_t",
           "local_path": None, "size": 1, "digest": "d"}
    assert runs_mod._materialize_store(esc) is None
    assert fetches["n"] == 0


# ── 11. lookup (resolve_project_run_output) confirms WITHOUT moving bytes ──────

def test_lookup_does_not_fetch(monkeypatch):
    rid = _mk_run(weft_targets=["krn_look"], run_state="open")
    monkeypatch.setattr(runs_mod, "resolve_run_output_path", lambda r, n: None)
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_look": "mendel"})
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {})   # store, not a file
    monkeypatch.setattr(runs_mod, "_live_inventory",
                        lambda t, **kw: _stable_inv([{"path": "store.zarr/zarr.json", "bytes": 4},
                                                     {"path": "store.zarr/chunks/0", "bytes": 40}]))
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    touched = {"retain": 0}
    monkeypatch.setattr(retmod, "retain",
                        lambda *a, **k: touched.__setitem__("retain", touched["retain"] + 1) or {})
    import core.data.datasets as dsmod
    touched_fetch = {"n": 0}
    monkeypatch.setattr(dsmod, "fetch",
                        lambda *a, **k: touched_fetch.__setitem__("n", touched_fetch["n"] + 1) or {"ok": True})
    monkeypatch.setattr(dsmod, "register_source", lambda s, **k: {"home": {"site": "mendel", "path": s}})

    hit = runs_mod.resolve_project_run_output("store.zarr")
    assert hit == (rid, "store.zarr")          # remote MARKER, not an on-disk path
    assert touched["retain"] == 0 and touched_fetch["n"] == 0   # lookup moved NO bytes


# ── 12. a grown live store re-fetches (no stale cache hit) ─────────────────────

def test_live_store_regrow_triggers_refetch(monkeypatch):
    rid = _mk_run(weft_targets=["krn_grow"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_grow": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(runs_mod, "resolve_run_output_path", lambda r, n: None)
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    state = {"inv": _stable_inv([{"path": "s.zarr/zarr.json", "bytes": 2},
                                 {"path": "s.zarr/chunks/0", "bytes": 100}])}
    monkeypatch.setattr(runs_mod, "_live_inventory", lambda t, **kw: state["inv"])

    import core.data.datasets as dsmod
    calls = {"n": 0}
    monkeypatch.setattr(dsmod, "register_source",
                        lambda s, **k: {"home": {"site": "mendel", "path": s}})

    def _fetch(meta, to_path, **kw):
        calls["n"] += 1
        os.makedirs(to_path, exist_ok=True)
        open(os.path.join(to_path, "zarr.json"), "w").write("{}")
        return {"ok": True, "path": to_path}
    monkeypatch.setattr(dsmod, "fetch", _fetch)

    runs_mod.resolve_run_store(rid, "s.zarr")
    assert calls["n"] == 1
    # the run keeps writing → inventory total changes
    state["inv"] = _stable_inv([{"path": "s.zarr/zarr.json", "bytes": 2},
                                {"path": "s.zarr/chunks/0", "bytes": 100},
                                {"path": "s.zarr/chunks/1", "bytes": 100}])
    runs_mod.resolve_run_store(rid, "s.zarr")
    assert calls["n"] == 2                       # stale → re-fetch, not a frozen hit


# ── 13. concurrent fetches use DISTINCT temp dirs (no shared .partial) ─────────

def test_concurrent_fetch_distinct_temp_dirs(monkeypatch):
    rid = _mk_run(weft_targets=["krn_cc"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_cc": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(runs_mod, "resolve_run_output_path", lambda r, n: None)
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs_mod, "_live_inventory",
                        lambda t, **kw: _stable_inv([{"path": "c.zarr/x", "bytes": 5}]))
    seen = []

    def _fake_dp(abs_path, site, dest, *, force=False):
        seen.append(dest)          # capture the temp dir; report failure so dest is never created
        return False
    monkeypatch.setattr(runs_mod, "_data_plane_fetch", _fake_dp)

    runs_mod.resolve_run_store(rid, "c.zarr")
    runs_mod.resolve_run_store(rid, "c.zarr")
    assert len(seen) == 2 and seen[0] != seen[1]      # no two fetches share a temp path
    assert all(".partial." in s for s in seen)


# ── 14. FULL LIFECYCLE: produce remotely → list honestly → open here → settle ──

def test_lifecycle_remote_produce_open_here_then_settle(monkeypatch, tmp_path):
    """The invariant's end-to-end guard, through the REAL tier machinery (only
    the substrate is mocked): a file produced on a remote node (a) surfaces on
    the run listing with a LIVE link (presentation parity), (b) serves through
    the /file route via a transparent fetch, (c) re-opens from cache while
    unchanged, (d) re-fetches after a SAME-SIZE rewrite (mtime moves → digest
    moves), and (e) after settlement resolves from the local retained tree
    with no further transport."""
    import json as _json
    import core.exec.artifacts as artmod
    payload, payload2 = b"v1-bytes-remote", b"v2-bytes-remote"   # same length? no:
    payload2 = b"v2-bytes-remot3"                                # same SIZE as v1
    assert len(payload) == len(payload2)
    rid = _mk_run(thread_id="t", run_state="open", weft_targets=["krn_lc"])
    rel = "res.csv"
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_lc": "mendel"})
    rows = {"rows": []}
    monkeypatch.setattr(retmod, "retained", lambda **kw: list(rows["rows"]))
    monkeypatch.setattr(retmod, "inventory", lambda t, **kw: {"entries": []})
    stat = {"exists": True, "bytes": len(payload), "mtime": 100}
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: dict(stat))
    monkeypatch.setattr(retmod, "file_stat", lambda t, r: dict(stat))
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda r: [
        {"original_name": rel, "url": None, "kind": "file", "size": len(payload)}])
    content = {"cur": payload}
    reads = {"n": 0}

    def _read(t, r, max_bytes=1 << 20):
        reads["n"] += 1
        return {"bytes_b64": base64.b64encode(content["cur"]).decode(),
                "truncated": False}
    monkeypatch.setattr(retmod, "file_read", _read)

    # (a) the LIST surface names what was captured and links it live
    view = runs_mod.run_durable_view(rid)
    row = next(f for f in view["files"] if f["rel"] == rel)
    assert row["url"] == f"/api/runs/{rid}/file?rel={rel}"

    # (b) the SERVE surface brings it home transparently and streams it
    resp = rt.run_file(rid, rel)
    assert Path(resp.path).read_bytes() == payload and reads["n"] == 1
    # (c) unchanged → cache hit, zero further transport
    resp = rt.run_file(rid, rel)
    assert reads["n"] == 1
    # (d) SAME-SIZE rewrite on the still-open run → digest moves → re-fetch
    stat["mtime"] = 200
    content["cur"] = payload2
    resp = rt.run_file(rid, rel)
    assert Path(resp.path).read_bytes() == payload2 and reads["n"] == 2
    # (e) settlement: the keep lands in a LOCAL retained tree (sidecar-listed);
    # the local tier answers, transport count frozen
    loc = tmp_path / "settled"
    loc.mkdir()
    (loc / ".weft-run.json").write_text(_json.dumps({"files": [{"path": rel}]}))
    (loc / rel).write_bytes(b"settled-bytes")
    rows["rows"] = [{"state": "done", "target": "krn_lc", "site": "local",
                     "location": str(loc), "in_place": False}]
    p = runs_mod.resolve_run_file(rid, rel)
    assert p == os.path.realpath(str(loc / rel)) and reads["n"] == 2


# ── 15. store SAME-SIZE rewrite (mtime-only change) invalidates the cache ─────

def test_live_store_same_size_rewrite_refetches(monkeypatch):
    """Corollary: caching is only valid for immutable things. A live store
    rewritten IN PLACE (same file count, same bytes, new mtimes) must read as
    stale — the freshness digest includes mtimes, so totals-parity can't fake
    currency."""
    rid = _mk_run(weft_targets=["krn_mt"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_mt": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    state = {"inv": _stable_inv([{"path": "m.zarr/a", "bytes": 100, "mtime": 1}])}
    monkeypatch.setattr(runs_mod, "_live_inventory", lambda t, **kw: state["inv"])
    import core.data.datasets as dsmod
    calls = {"n": 0}
    monkeypatch.setattr(dsmod, "register_source",
                        lambda s, **k: {"home": {"site": "mendel", "path": s}})

    def _fetch(meta, to_path, **kw):
        calls["n"] += 1
        os.makedirs(to_path, exist_ok=True)
        open(os.path.join(to_path, "a"), "wb").write(b"x" * 100)
        return {"ok": True, "path": to_path}
    monkeypatch.setattr(dsmod, "fetch", _fetch)

    runs_mod.resolve_run_store(rid, "m.zarr")
    assert calls["n"] == 1
    state["inv"] = _stable_inv([{"path": "m.zarr/a", "bytes": 100, "mtime": 2}])
    runs_mod.resolve_run_store(rid, "m.zarr")
    assert calls["n"] == 2                     # same totals, new mtime → re-fetch


# ── 16. entity download: remote-produced file serves / 413s honestly ──────────

def test_entity_download_remote_fallback_and_413(monkeypatch):
    """The SERVE surface for entity-backed artifacts: a dangling /artifacts
    serving copy no longer 404s a remotely-produced file — the entity's own
    exec reference resolves through the canonical pair; past the gate the
    answer names the site (honest 413, never 'missing on disk' while the
    bytes durably exist)."""
    from fastapi import HTTPException
    import main as mainmod
    from core.graph import exec_records as er
    payload = b"entity-download-bytes"
    rid = _mk_run(thread_id="t", run_state="open", weft_targets=["krn_ed"])
    ent_row = {"id": "ent_dl", "type": "figure", "title": "Remote fig",
               "artifact_path": "/artifacts/default/gone.png",
               "exec_id": "exec_ed", "metadata": {"original_name": "fig.png"}}

    def _ge(x):
        if x == "ent_dl":
            return ent_row
        return {"id": rid, "metadata": {"run_state": "open",
                                        "weft_targets": ["krn_ed"]}} if x == rid else None
    monkeypatch.setattr(mainmod, "get_entity", _ge)
    monkeypatch.setattr(runs_mod, "get_entity", _ge)
    monkeypatch.setattr(er, "get",
                        lambda x: {"run_id": rid} if x == "exec_ed" else None)
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_ed": "mendel"})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    stat = {"exists": True, "bytes": len(payload), "mtime": 10}
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: dict(stat))
    monkeypatch.setattr(retmod, "file_read", lambda t, r, max_bytes=1 << 20: {
        "bytes_b64": base64.b64encode(payload).decode(), "truncated": False})

    resp = mainmod.entities_download("ent_dl")
    assert Path(resp.path).read_bytes() == payload
    # oversize → an honest site-naming 413, not a 404
    stat.update(bytes=300 * 1024 * 1024, mtime=11)
    try:
        mainmod.entities_download("ent_dl")
        raise AssertionError("expected HTTPException")
    except HTTPException as e:
        assert e.status_code == 413 and "mendel" in e.detail


# ── 17. concurrent install: a CURRENT dest is never destroyed ─────────────────

def test_concurrent_store_install_keeps_fresh_dest(monkeypatch):
    """Install-time race: while one open is fetching, another finishes and
    installs a CURRENT copy. The slower install must KEEP the fresh dest
    (discarding its own temp) — the swap may only ever replace STALE bytes,
    so a path already handed to a viewer never blinks out of existence."""
    rid = _mk_run(weft_targets=["krn_race"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_race": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(runs_mod, "_live_file_stat", lambda t, r: {})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    inv = _stable_inv([{"path": "r.zarr/a", "bytes": 3, "mtime": 5}])
    monkeypatch.setattr(runs_mod, "_live_inventory", lambda t, **kw: inv)
    digest = runs_mod._store_members("krn_race", "r.zarr")["digest"]
    assert digest

    def _dp(abs_path, site, dest, *, force=False):
        os.makedirs(dest, exist_ok=True)               # our fetch lands in a temp
        open(os.path.join(dest, "ours"), "w").write("x")
        real_dest = dest.split(".partial.")[0]         # …meanwhile a peer installs
        os.makedirs(real_dest, exist_ok=True)
        open(os.path.join(real_dest, "PEER"), "w").write("p")
        runs_mod._stamp_write(real_dest, digest)
        return True
    monkeypatch.setattr(runs_mod, "_data_plane_fetch", _dp)

    p = runs_mod.resolve_run_store(rid, "r.zarr")
    assert p and os.path.isfile(os.path.join(p, "PEER"))   # fresh peer copy kept
    assert not os.path.exists(os.path.join(p, "ours"))     # our temp discarded


# ── 18. single-file fetches are atomic: unique temp, then replace ─────────────

def test_file_fetch_atomic_temp_then_replace(monkeypatch):
    """The data-plane file lane writes to a unique .partial temp and installs
    with os.replace — a concurrent reader can never see a half-written dest,
    and a failed fetch leaves NO dest at all."""
    size = 20 * 1024 * 1024
    rid = _mk_run(weft_targets=["krn_at"], run_state="open")
    monkeypatch.setattr(runs_mod, "_kernel_site_map", lambda: {"krn_at": "mendel"})
    monkeypatch.setattr(runs_mod, "_site_root", lambda site: "/remote/.weft")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs_mod, "_live_file_stat",
                        lambda t, r: {"exists": True, "bytes": size, "mtime": 7})
    import core.data.datasets as dsmod
    seen = {}
    monkeypatch.setattr(dsmod, "register_source",
                        lambda s, **k: {"home": {"site": "mendel", "path": s}})

    def _ok(meta, to_path, **kw):
        seen["to_path"] = to_path
        os.makedirs(os.path.dirname(to_path) or ".", exist_ok=True)
        open(to_path, "wb").write(b"z" * 128)
        return {"ok": True, "path": to_path}
    monkeypatch.setattr(dsmod, "fetch", _ok)
    p = runs_mod.resolve_run_file(rid, "atomic.bin")
    assert p and os.path.isfile(p)
    assert ".partial." in seen["to_path"]          # fetched into a unique temp…
    assert not os.path.exists(seen["to_path"])     # …which was atomically moved

    def _fail(meta, to_path, **kw):
        seen["to_path"] = to_path
        os.makedirs(os.path.dirname(to_path) or ".", exist_ok=True)
        open(to_path, "wb").write(b"partial")
        return {"ok": False}
    monkeypatch.setattr(dsmod, "fetch", _fail)
    assert runs_mod.resolve_run_file(rid, "atomic2.bin") is None
    dest2 = seen["to_path"].split(".partial.")[0]
    assert not os.path.exists(dest2)               # failure leaves no dest


_TESTS = [
    test_resolve_run_file_remote_tier_fetches_then_caches,
    test_oversize_remote_file_route_names_site,
    test_local_fast_path_no_transport,
    test_produced_entries_carry_remote_site,
    test_produced_entries_omit_site_when_local,
    test_remote_dataset_tree_lists_real_files,
    test_pagoda3_resolve_source_uses_canonical,
    test_pagoda3_resolve_source_names_site_when_unresolvable,
    test_live_kernel_store_brings_back_via_data_plane,
    test_mid_band_single_file_live_kernel_uses_data_plane,
    test_unknown_size_declines_and_route_names_site,
    test_traversal_rejected_file_and_store,
    test_lookup_does_not_fetch,
    test_live_store_regrow_triggers_refetch,
    test_concurrent_fetch_distinct_temp_dirs,
    test_lifecycle_remote_produce_open_here_then_settle,
    test_live_store_same_size_rewrite_refetches,
    test_entity_download_remote_fallback_and_413,
    test_concurrent_store_install_keeps_fresh_dest,
    test_file_fetch_atomic_temp_then_replace,
]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            params = inspect.signature(t).parameters
            kw = {}
            if "monkeypatch" in params:
                kw["monkeypatch"] = mp
            if "tmp_path" in params:
                kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_t_", dir=_RT))
            t(**kw)
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
