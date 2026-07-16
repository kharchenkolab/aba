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
         "location": "/somewhere/krn_1"},
    ])
    monkeypatch.setattr(retmod, "inventory", lambda t: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "umap.png", "url": "/artifacts/p/x.png",
         "kind": "figure", "size": 2048},                       # small surfaced → kept (aba store)
        {"original_name": "big.h5ad", "url": None, "kind": "file",
         "size": _BIG},                                          # large + in done sidecar → kept on site
        {"original_name": "model.pt", "url": None, "kind": "file",
         "size": _BIG},                                          # large + a pending row exists → pinned
    ])
    view = runsmod.run_durable_view("run-1")
    by = {f["rel"]: f for f in view["files"]}
    assert by["umap.png"]["state"] == "kept" and by["umap.png"]["url"] == "/artifacts/p/x.png"
    assert by["big.h5ad"]["state"] == "kept" and by["big.h5ad"]["badge"] == "on local"
    assert by["big.h5ad"]["url"].startswith("/api/runs/run-1/file?rel=")   # tier-resolved
    assert by["model.pt"]["state"] == "pinned-pending"
    assert by["model.pt"]["badge"] == "large · keeps the version at run settlement"
    assert by["model.pt"]["large"] is True
    assert view["summary"] == {"kept": 2, "pinned_pending": 1, "in_sandbox": 0,
                               "cleared": 0, "total": 3}


def test_durable_view_in_sandbox_vs_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(runsmod, "get_entity",
                        lambda rid: {"id": rid, "metadata": {"weft_targets": ["krn_9"]}})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])          # nothing retained
    monkeypatch.setattr(retmod, "inventory",
                        lambda t: {"entries": [{"path": "still.csv", "bytes": 5, "mtime": 1}]})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda rid: [
        {"original_name": "still.csv", "url": None, "kind": "table", "size": 5},   # in inventory → in-sandbox
        {"original_name": "gone.dat", "url": None, "kind": "file", "size": 9},     # nowhere → cleared
    ])
    by = {f["rel"]: f for f in runsmod.run_durable_view("run-2")["files"]}
    assert by["still.csv"]["state"] == "in-sandbox"
    assert by["gone.dat"]["state"] == "cleared" and by["gone.dat"]["url"] is None


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
    assert qc["path"] == "samples/A/qc.csv" and qc["kind"] == "file" and qc["state"] == "kept"
    # file node carries durable fields + server url in artifact_path
    big = top["big.h5ad"]
    assert big["state"] == "kept" and big["large"] is True
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
    assert by["big.h5ad"]["state"] == "kept" and by["big.h5ad"]["badge"] == "on hpc"
    assert by["big.h5ad"]["site"] == "hpc"
    assert by["figs/umap.png"]["state"] == "kept"       # matched figs/**
    assert by["scratch.tmp"]["state"] != "kept"         # excluded → not durable


_TESTS = [test_durable_view_states, test_durable_view_in_sandbox_vs_cleared,
          test_resolve_run_file_retained_then_sandbox_then_escape,
          test_durable_tree_nests_with_durable_fields,
          test_durable_view_remote_in_place_kept_via_selection]


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
