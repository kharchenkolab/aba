"""P3 serving spine — the catalog-backed resolve_output facade + serve-in-place.

Covers:
  * `_sidecar_resolve` — catalog-first resolution inside a retained location
    (exact rel / directory-store prefix group / basename), no directory walk of
    caller input, escape-safe by construction.
  * `resolve_output` — tier reporting: retained → live jobdir → scratch, with
    {local_path, rel, root, locality, durability, kind}; `rel` on the live tier
    is sandbox-relative (retain-include ready).
  * `_serve_native_store` — a store under the WEFT WORKSPACE (retained tree or
    live jobdir) is SYMLINKED into pagoda3/, never copied (P3 serve-in-place);
    only a path outside both project and weft workspace is copied.

Run: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=backend pytest tests/test_serving_spine.py
     (or standalone: python tests/test_serving_spine.py)
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_spine_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from content.bio.lifecycle import runs  # noqa: E402


def _mk_sidecar(loc: Path, rels: list) -> None:
    loc.mkdir(parents=True, exist_ok=True)
    (loc / ".weft-run.json").write_text(
        json.dumps({"files": [{"path": r} for r in rels]}))
    for r in rels:
        p = loc / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def test_sidecar_resolve_exact_and_basename(tmp_path):
    loc = tmp_path / "ret"
    _mk_sidecar(loc, ["summary.csv", "sub/deep.csv"])
    assert runs._sidecar_resolve(str(loc), "summary.csv") == \
        os.path.realpath(str(loc / "summary.csv"))
    # basename match reaches a nested rel
    assert runs._sidecar_resolve(str(loc), "deep.csv") == \
        os.path.realpath(str(loc / "sub" / "deep.csv"))


def test_sidecar_resolve_store_prefix_group(tmp_path):
    """A directory store is enumerated per-file in the sidecar; resolving the
    store NAME returns the store DIRECTORY (the prefix group), not a chunk."""
    loc = tmp_path / "ret"
    _mk_sidecar(loc, ["cube.zarr/.zmetadata", "cube.zarr/0.0", "cube.zarr/c/1"])
    hit = runs._sidecar_resolve(str(loc), "cube.zarr")
    assert hit == os.path.realpath(str(loc / "cube.zarr")) and os.path.isdir(hit)


def test_sidecar_resolve_absent(tmp_path):
    loc = tmp_path / "ret"
    _mk_sidecar(loc, ["a.csv"])
    assert runs._sidecar_resolve(str(loc), "ghost.csv") is None
    assert runs._sidecar_resolve(str(tmp_path / "nowhere"), "a.csv") is None


def test_resolve_output_retained_tier(tmp_path, monkeypatch):
    import core.compute.retention as retmod
    loc = tmp_path / "ret"
    _mk_sidecar(loc, ["keep.csv"])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{"state": "done"}])
    monkeypatch.setattr(retmod, "location_path", lambda row: str(loc))
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": None})
    info = runs.resolve_output("r", "keep.csv")
    assert info["durability"] == "retained" and info["locality"] == "local"
    assert info["kind"] == "file" and info["rel"] == "keep.csv"
    assert info["local_path"] == os.path.realpath(str(loc / "keep.csv"))


def test_resolve_output_live_tier_dir_store(tmp_path, monkeypatch):
    """A fresh store in the live jobdir: durability=live, kind=dir, and `rel`
    is sandbox-relative (usable directly as a retain include literal)."""
    import core.compute.retention as retmod
    jd = tmp_path / "jobdir"
    store = jd / "out" / "cube.zarr"
    store.mkdir(parents=True)
    (store / "0.0").write_text("c")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [str(jd)])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": None})
    info = runs.resolve_output("r", "cube.zarr")
    assert info["durability"] == "live" and info["kind"] == "dir"
    assert info["rel"] == "out/cube.zarr"
    assert info["local_path"] == os.path.realpath(str(store))


def test_resolve_output_retained_wins_over_live(tmp_path, monkeypatch):
    import core.compute.retention as retmod
    loc = tmp_path / "ret"
    _mk_sidecar(loc, ["dual.csv"])
    jd = tmp_path / "jobdir"
    jd.mkdir()
    (jd / "dual.csv").write_text("live")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{"state": "done"}])
    monkeypatch.setattr(retmod, "location_path", lambda row: str(loc))
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [str(jd)])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": None})
    info = runs.resolve_output("r", "dual.csv")
    assert info["durability"] == "retained"
    assert info["local_path"].startswith(os.path.realpath(str(loc)))


def test_resolve_output_scratch_fallback_and_absent(tmp_path, monkeypatch):
    import core.compute.retention as retmod
    ap = tmp_path / "scratch"
    ap.mkdir()
    (ap / "note.txt").write_text("n")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": str(ap)})
    info = runs.resolve_output("r", "note.txt")
    assert info["durability"] == "scratch" and info["rel"] == "note.txt"
    assert runs.resolve_output("r", "ghost.txt") is None


def test_resolve_entity_output_uses_exec_reference(tmp_path, monkeypatch):
    """P5 shim: an entity whose /artifacts serving copy is gone still resolves —
    via its OWN durable reference (exec_id → run_id → metadata.original_name)
    through the facade."""
    import core.compute.retention as retmod
    from core.graph import exec_records as er
    loc = tmp_path / "ret"
    _mk_sidecar(loc, ["big.h5ad"])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{"state": "done"}])
    monkeypatch.setattr(retmod, "location_path", lambda row: str(loc))
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [])
    monkeypatch.setattr(runs, "get_entity", lambda eid: {
        "id": eid, "exec_id": "exec_1", "artifact_path": "/artifacts/p/gone.h5ad",
        "metadata": {"original_name": "big.h5ad"},
    } if eid == "ent_1" else {"artifact_path": None})
    monkeypatch.setattr(er, "get", lambda x: {"run_id": "run_1"})
    info = runs.resolve_entity_output("ent_1")
    assert info and info["durability"] == "retained"
    assert info["local_path"] == os.path.realpath(str(loc / "big.h5ad"))


def test_resolve_entity_output_none_without_reference(monkeypatch):
    """No exec_id / no original_name → nothing to resolve (a user-authored file
    stays aba's, per the serving model's §8 boundary) — and never raises."""
    monkeypatch.setattr(runs, "get_entity", lambda eid: {
        "id": eid, "artifact_path": "/artifacts/p/x.png", "metadata": {}})
    assert runs.resolve_entity_output("ent_x") is None
    monkeypatch.setattr(runs, "get_entity", lambda eid: None)
    assert runs.resolve_entity_output("ghost") is None


def test_serve_native_store_symlinks_weft_workspace_store(tmp_path, monkeypatch):
    """P3 serve-in-place: a store under the weft workspace (retained tree /
    live jobdir) is symlinked, not copied."""
    from content.bio.viewers.launchers import pagoda3 as p3
    from core.compute import adapter as admod
    ws = tmp_path / "weft"
    store = ws / "runs" / "ana_1" / "krn_1" / "cube.lstar.zarr"
    store.mkdir(parents=True)
    (store / "0.0").write_text("chunk")
    monkeypatch.setattr(admod, "weft_workspace", lambda: ws)
    project = tmp_path / "project"
    project.mkdir()
    out = p3._serve_native_store(store, project / "pagoda3", "cube-x.lstar.zarr", project)
    assert out.is_symlink() and out.resolve() == store.resolve()


def test_serve_native_store_still_copies_true_outsider(tmp_path, monkeypatch):
    from content.bio.viewers.launchers import pagoda3 as p3
    from core.compute import adapter as admod
    monkeypatch.setattr(admod, "weft_workspace", lambda: tmp_path / "weft")
    outsider = tmp_path / "elsewhere" / "ext.lstar.zarr"
    outsider.mkdir(parents=True)
    (outsider / "0.0").write_text("chunk")
    project = tmp_path / "project"
    project.mkdir()
    out = p3._serve_native_store(outsider, project / "pagoda3", "ext-x.lstar.zarr", project)
    assert out.is_dir() and not out.is_symlink()
    assert (out / "0.0").read_text() == "chunk"


_TESTS = [
    test_sidecar_resolve_exact_and_basename,
    test_sidecar_resolve_store_prefix_group,
    test_sidecar_resolve_absent,
    test_resolve_output_retained_tier,
    test_resolve_output_live_tier_dir_store,
    test_resolve_output_retained_wins_over_live,
    test_resolve_output_scratch_fallback_and_absent,
    test_resolve_entity_output_uses_exec_reference,
    test_resolve_entity_output_none_without_reference,
    test_serve_native_store_symlinks_weft_workspace_store,
    test_serve_native_store_still_copies_true_outsider,
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
        kw = {}
        params = inspect.signature(t).parameters
        if "tmp_path" in params:
            kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_spine_t_"))
        if "monkeypatch" in params:
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
