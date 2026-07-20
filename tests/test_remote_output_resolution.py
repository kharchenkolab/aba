"""Remote-output resolution lifecycle (Layer 0 + Layer 1): a Run output produced on
a NON-local site is resolvable / servable / listable from the controller.

Everything runs against a temp entity DB with `core.compute.retention` (and the
compute adapter) monkeypatched — no real substrate. Covers:
 1. resolve_run_file remote tier: small remote file fetched once, cached, reused.
 2. oversize remote file → transparent resolve None; /file route names the site.
 3. local fast path resolves with NO transport calls.
 4. produced[] entries carry the step's site for a remote step.
 5. tree: a by-reference remote dataset lists descriptor.top real names, not a .bin.
 6. pagoda3 _resolve_source: routes through the canonical resolver; names the site.

Run: python tests/test_remote_output_resolution.py
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

init_db()


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


_TESTS = [
    test_resolve_run_file_remote_tier_fetches_then_caches,
    test_oversize_remote_file_route_names_site,
    test_local_fast_path_no_transport,
    test_produced_entries_carry_remote_site,
    test_produced_entries_omit_site_when_local,
    test_remote_dataset_tree_lists_real_files,
    test_pagoda3_resolve_source_uses_canonical,
    test_pagoda3_resolve_source_names_site_when_unresolvable,
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
            t(mp) if "monkeypatch" in inspect.signature(t).parameters else t()
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
