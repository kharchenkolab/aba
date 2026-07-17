"""P2d: run_durable_view + resolve_run_file — the Files-panel durable model.

Unit-tested against the GROUNDED weft shapes (misc/output_durability.md §6.1b): a real
`.weft-run.json` sidecar on disk, retained() rows with string `location`, done/pending
states, and inventory paths. No live weft needed.

Run: python tests/test_run_durable_view.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_dv_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from content.bio.lifecycle import runs as runsmod  # noqa: E402
import core.exec.artifacts as artmod  # noqa: E402
import core.compute.retention as retmod  # noqa: E402
from core.exec.run import _MAX_HARVEST_BYTES  # noqa: E402

_BIG = _MAX_HARVEST_BYTES + 1_000_000


def _retained_dir(tmp: Path, target: str, files: dict) -> str:
    """Build a runs/<label>/<target>/ tree with a v1 sidecar (grounded shape)."""
    d = tmp / "runs" / "lbl" / target
    d.mkdir(parents=True)
    entries = []
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        entries.append({"path": rel, "bytes": len(content), "mtime": 1})
    (d / ".weft-run.json").write_text(json.dumps({
        "schema": "weft-run:v1", "target": target, "kind": "kernel",
        "site": "local", "label": "lbl", "files": entries}))
    return str(d)


def test_durable_view_states(tmp_path, monkeypatch):
    loc = _retained_dir(tmp_path, "krn_1", {"big.h5ad": "x" * 10})
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_1"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "done", "site": "local", "in_place": 1, "location": loc},
        {"state": "pinned-pending", "site": "local", "in_place": 0,
         "location": "/somewhere/krn_1",
         "selection": json.dumps({"include": ["model.pt"]})},   # per-file saving: covers model.pt
    ])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png", "url": "/artifacts/p/x.png",
         "kind": "figure", "size": 2048},                       # small surfaced, not weft-kept → in-store
        {"original_name": "big.h5ad", "url": None, "kind": "file",
         "size": _BIG},                                          # large + in done sidecar → retained on site
        {"original_name": "model.pt", "url": None, "kind": "file",
         "size": _BIG},                                          # large + covered by pending → saving
    ])
    view = runsmod.run_durable_view("run-1")
    by = {f["rel"]: f for f in view["files"]}
    # weft-truth: the aba store copy is honestly "in-store", NOT a fake "retained"
    assert by["umap.png"]["state"] == "in-store" and by["umap.png"]["url"] == "/artifacts/p/x.png"
    assert by["big.h5ad"]["state"] == "retained" and by["big.h5ad"]["badge"] == "on local"
    assert by["big.h5ad"]["url"].startswith("/api/runs/run-1/file?rel=")   # tier-resolved
    assert by["model.pt"]["state"] == "saving"
    assert by["model.pt"]["badge"] == "saving… · keeps the version at run settlement"
    assert by["model.pt"]["large"] is True
    assert view["summary"] == {"retained": 1, "saving": 1, "in_store": 1,
                               "at_risk": 0, "in_sandbox": 0, "cleared": 0, "total": 3}


def test_durable_view_serves_retained_local_directly_from_weft(tmp_path, monkeypatch):
    """R4: a small figure that is BOTH in aba's /artifacts store (has a url) AND weft-retained
    LOCALLY is served straight from weft's durable copy via /file — not the store cache. The
    store url stays the fallback only for files with no local weft copy (in-store)."""
    loc = _retained_dir(tmp_path, "krn_x", {"umap.png": "img", "figs/pca.png": "img2"})
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_x"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "done", "site": "local", "in_place": 0, "location": loc}])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png", "url": "/artifacts/p/x.png", "kind": "figure", "size": 2048},
        {"original_name": "orphan.png", "url": "/artifacts/p/o.png", "kind": "figure", "size": 512},
    ])
    by = {f["rel"]: f for f in runsmod.run_durable_view("run-x")["files"]}
    # retained locally → served from weft directly, NOT the /artifacts cache url
    assert by["umap.png"]["state"] == "retained"
    assert by["umap.png"]["url"] == "/api/runs/run-x/file?rel=umap.png"
    assert by["umap.png"]["url"] != "/artifacts/p/x.png"
    # not in the retained tree → in-store, still served from the cache (the fallback)
    assert by["orphan.png"]["state"] == "in-store"
    assert by["orphan.png"]["url"] == "/artifacts/p/o.png"


def test_durable_view_in_sandbox_vs_cleared_via_stat(tmp_path, monkeypatch):
    """B1a: a live on-disk stat (weft run_file_stat) is authoritative — still.csv exists,
    gone.dat was swept. Live size fills a produced size of 0."""
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_9"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])          # nothing retained
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(retmod, "file_stat", lambda t, rel:
                        {"exists": True, "bytes": 4096} if rel == "still.csv"
                        else {"exists": False})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "still.csv", "url": None, "kind": "table", "size": 0},   # on disk
        {"original_name": "gone.dat", "url": None, "kind": "file", "size": 9},     # swept
    ])
    by = {f["rel"]: f for f in runsmod.run_durable_view("run-2")["files"]}
    assert by["still.csv"]["state"] == "in-sandbox" and by["still.csv"]["bytes"] == 4096
    assert by["gone.dat"]["state"] == "cleared" and by["gone.dat"]["url"] is None


def test_durable_view_falls_back_to_inventory_proxy_when_stat_unavailable(tmp_path, monkeypatch):
    """If a stat can't be performed (weft error), fall back to the terminal-inventory proxy."""
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_9"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(retmod, "inventory",
                        lambda t: {"entries": [{"path": "was.csv", "bytes": 5, "mtime": 1}]})

    def _boom(t, rel):
        raise RuntimeError("weft unreachable")
    monkeypatch.setattr(retmod, "file_stat", _boom)
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "was.csv", "url": None, "kind": "table", "size": 5},   # in inventory
        {"original_name": "never.dat", "url": None, "kind": "file", "size": 9},  # nowhere
    ])
    by = {f["rel"]: f for f in runsmod.run_durable_view("run-3")["files"]}
    # stat raised (performed=False) → proxy: inventoried → in-sandbox; else cleared
    assert by["was.csv"]["state"] == "in-sandbox"
    assert by["never.dat"]["state"] == "cleared"


def test_resolve_run_file_retained_then_sandbox_then_escape(tmp_path, monkeypatch):
    loc = _retained_dir(tmp_path, "krn_r", {"keep.txt": "precious", "sub/n.csv": "a,b"})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "done", "location": loc, "site": "local", "in_place": 0}])
    # sandbox fallback base (for a file NOT retained)
    sandbox = tmp_path / "sandbox"; sandbox.mkdir()
    (sandbox / "live.png").write_text("img")
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "artifact_path": str(sandbox)})

    _rp = os.path.realpath   # resolver canonicalizes (macOS /var → /private/var)
    assert runsmod.resolve_run_file("r", "keep.txt") == _rp(os.path.join(loc, "keep.txt"))
    assert runsmod.resolve_run_file("r", "sub/n.csv") == _rp(os.path.join(loc, "sub/n.csv"))
    assert runsmod.resolve_run_file("r", "live.png") == _rp(str(sandbox / "live.png"))  # fallback
    assert runsmod.resolve_run_file("r", "../../etc/passwd") is None               # escape rejected
    assert runsmod.resolve_run_file("r", "nope.txt") is None


def test_durable_tree_nests_with_durable_fields(tmp_path, monkeypatch):
    loc = _retained_dir(tmp_path, "krn_t", {"big.h5ad": "x" * 10})
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_t"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "done", "site": "local", "in_place": 0, "location": loc}])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png", "url": "/artifacts/p/x.png", "kind": "figure", "size": 2048},
        {"original_name": "samples/A/qc.csv", "url": "/artifacts/p/y.csv", "kind": "table", "size": 30},
        {"original_name": "big.h5ad", "url": None, "kind": "file", "size": _BIG},
    ])
    tree = runsmod.run_durable_tree("run-t")
    assert tree["kind"] == "root"
    top = {c["name"]: c for c in tree["children"]}
    # folder sorts before files; nested file lives under samples/A/
    assert top["samples"]["kind"] == "folder"
    a = {c["name"]: c for c in top["samples"]["children"]}["A"]
    qc = a["children"][0]
    assert qc["path"] == "samples/A/qc.csv" and qc["kind"] == "file" and qc["state"] == "in-store"
    # file node carries durable fields + server url in artifact_path
    big = top["big.h5ad"]
    assert big["state"] == "retained" and big["large"] is True
    assert big["artifact_path"].startswith("/api/runs/run-t/file?rel=")   # tier-resolved
    umap = top["umap.png"]
    assert umap["artifact_path"] == "/artifacts/p/x.png"                  # aba store url
    assert tree["summary"]["total"] == 3


def test_durable_view_remote_in_place_kept_via_selection(tmp_path, monkeypatch):
    """Remote in-place retain (storage_durable site, §5.1): the retained bytes stay on
    the site, so there's no locally-readable sidecar. The view must still show them KEPT
    (on <site>) by matching produced paths against the row's retained selection."""
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_r"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{
        "state": "done", "site": "hpc", "in_place": 1,
        "location": "/groups/lab/weft-retained/runs/lbl/krn_r",   # not on this box
        "selection": json.dumps({"include": ["big.h5ad", "figs/**"],
                                 "exclude": ["*.tmp"]}),
    }])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "big.h5ad", "url": None, "kind": "file", "size": _BIG},
        {"original_name": "figs/umap.png", "url": None, "kind": "figure", "size": 40},
        {"original_name": "scratch.tmp", "url": None, "kind": "file", "size": 5},  # excluded
    ])
    by = {f["rel"]: f for f in runsmod.run_durable_view("run-r")["files"]}
    assert by["big.h5ad"]["state"] == "retained" and by["big.h5ad"]["badge"] == "on hpc"
    assert by["big.h5ad"]["site"] == "hpc"
    assert by["figs/umap.png"]["state"] == "retained"   # matched figs/**
    assert by["scratch.tmp"]["state"] != "retained"     # excluded → not durable


def test_durable_view_dedups_repeated_filename(tmp_path, monkeypatch):
    """A filename produced by N cells returns N rows from artifacts_for_run; the
    panel shows ONE file per path, latest-version-wins, and counts must not inflate."""
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_1"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    # umap.png produced 3×: first two un-surfaced, the LAST surfaced to the store.
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png", "url": None, "kind": "figure", "size": 10},
        {"original_name": "umap.png", "url": None, "kind": "figure", "size": 20},
        {"original_name": "umap.png", "url": "/artifacts/p/umap.png",
         "kind": "figure", "size": 30},
    ])
    view = runsmod.run_durable_view("run-d")
    rows = [f for f in view["files"] if f["rel"] == "umap.png"]
    assert len(rows) == 1                       # deduped, not 3
    assert rows[0]["url"] == "/artifacts/p/umap.png"   # latest (surfaced) won
    # a surfaced file with a store url but no weft retain is `in-store` (serving cache)
    assert view["summary"]["total"] == 1 and view["summary"]["in_store"] == 1


def test_durable_view_remote_in_place_has_no_local_file_url(tmp_path, monkeypatch):
    """A remote in-place `kept` file (no local sidecar) must NOT advertise a
    /api/runs/{id}/file URL — resolve_run_file can't read it, so the link 404s."""
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_r"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{
        "state": "done", "site": "hpc", "in_place": 1,
        "location": "/groups/lab/weft-retained/runs/lbl/krn_r",   # not on this box
        "selection": json.dumps({"include": ["big.h5ad"], "exclude": []}),
    }])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "big.h5ad", "url": None, "kind": "file", "size": _BIG}])
    f = runsmod.run_durable_view("run-r2")["files"][0]
    assert f["state"] == "retained" and f["site"] == "hpc"
    assert f["url"] is None                     # not locally servable → no dead link


def test_durable_view_selection_glob_does_not_span_slash(tmp_path, monkeypatch):
    """A `*.txt` retained selection must NOT claim a NESTED `sub/a.txt` as durable
    (fnmatch's `*` spans `/`; the path-aware matcher doesn't) — 'never lie'."""
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_g"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{
        "state": "done", "site": "hpc", "in_place": 1,
        "location": "/remote/only", "selection": json.dumps({"include": ["*.txt"]}),
    }])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(retmod, "file_stat", lambda t, rel: {"exists": False})  # nested → cleared
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "top.txt", "url": None, "kind": "file", "size": 5},
        {"original_name": "sub/deep.txt", "url": None, "kind": "file", "size": 5},
    ])
    by = {f["rel"]: f for f in runsmod.run_durable_view("run-g")["files"]}
    assert by["top.txt"]["state"] == "retained"      # *.txt matches a top-level file
    assert by["sub/deep.txt"]["state"] != "retained" # but NOT the nested one


def test_read_run_file_previews_in_sandbox(monkeypatch):
    """B1b: read_run_file decodes weft run_file_read's base64 preview across the targets."""
    import base64
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"metadata": {"weft_targets": ["krn_a"]}})
    monkeypatch.setattr(retmod, "file_read", lambda t, rel, max_bytes: {
        "bytes_b64": base64.b64encode(b"hello").decode(), "truncated": False, "bytes_total": 5})
    data, trunc, total = runsmod.read_run_file("run-1", "x.txt")
    assert data == b"hello" and trunc is False and total == 5


def test_read_run_file_flags_truncated(monkeypatch):
    import base64
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"metadata": {"weft_targets": ["krn_a"]}})
    monkeypatch.setattr(retmod, "file_read", lambda t, rel, max_bytes: {
        "bytes_b64": base64.b64encode(b"partial").decode(), "truncated": True,
        "bytes_total": 99_000_000})
    _data, trunc, total = runsmod.read_run_file("run-1", "big.bin")
    assert trunc is True and total == 99_000_000    # → route returns 413, not a broken partial


def test_read_run_file_unreadable_returns_none(monkeypatch):
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"metadata": {"weft_targets": ["krn_a"]}})

    def _boom(t, rel, max_bytes):
        raise RuntimeError("data.missing")
    monkeypatch.setattr(retmod, "file_read", _boom)
    assert runsmod.read_run_file("run-1", "gone")[0] is None


_TESTS = [test_durable_view_states,
          test_durable_view_serves_retained_local_directly_from_weft,
          test_durable_view_in_sandbox_vs_cleared_via_stat,
          test_durable_view_falls_back_to_inventory_proxy_when_stat_unavailable,
          test_resolve_run_file_retained_then_sandbox_then_escape,
          test_durable_tree_nests_with_durable_fields,
          test_durable_view_remote_in_place_kept_via_selection,
          test_durable_view_dedups_repeated_filename,
          test_durable_view_remote_in_place_has_no_local_file_url,
          test_durable_view_selection_glob_does_not_span_slash,
          test_read_run_file_previews_in_sandbox,
          test_read_run_file_flags_truncated,
          test_read_run_file_unreadable_returns_none]


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
        kw = {}
        sig = inspect.signature(t).parameters
        if "tmp_path" in sig:
            kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="dv_"))
        if "monkeypatch" in sig:
            kw["monkeypatch"] = mp
        try:
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
