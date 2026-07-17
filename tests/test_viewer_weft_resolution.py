"""Viewer resolution of weft Run outputs — resolve a fresh output (a `.lstar.zarr` STORE in the
live kernel jobdir, or a retained file) that isn't in the entity-graph files tree yet, so
get_viewer_url / the launch route work WITHOUT the user first `data_register`ing it.

Directory-aware (a `.zarr` store is a dir; the old resolve_run_file is file-only), tiered
(retained tree → live weft jobdir → run sandbox), escape-safe. Standalone harness (no pytest).

Run: python tests/test_viewer_weft_resolution.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_vwr_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from content.bio.lifecycle import runs  # noqa: E402


def test_search_root_finds_file(tmp_path):
    (tmp_path / "a.csv").write_text("x")
    assert runs._search_root_for(str(tmp_path), "a.csv") == os.path.realpath(str(tmp_path / "a.csv"))


def test_search_root_finds_directory_store(tmp_path):
    store = tmp_path / "out" / "processed.lstar.zarr"
    store.mkdir(parents=True)
    (store / ".zattrs").write_text("{}")      # zarr v2 dotfile — would be skipped by the tree graft
    (store / "0").write_text("chunk")
    hit = runs._search_root_for(str(tmp_path), "processed.lstar.zarr")
    assert hit == os.path.realpath(str(store)) and os.path.isdir(hit)


def test_search_root_prefers_dir_over_file(tmp_path):
    # both nested (so the exact-rel-join short-circuit misses and the walk tiebreaks):
    # a store DIR beats a same-named file — the viewer wants the store.
    (tmp_path / "a").mkdir(); (tmp_path / "a" / "store").write_text("f")
    d = tmp_path / "b" / "store"; d.mkdir(parents=True)
    assert os.path.isdir(runs._search_root_for(str(tmp_path), "store"))


def test_search_root_exact_child_wins(tmp_path):
    # a direct child matches immediately (cheap path), before any deeper walk
    (tmp_path / "processed.lstar.zarr").mkdir()
    assert runs._search_root_for(str(tmp_path), "processed.lstar.zarr") == \
        os.path.realpath(str(tmp_path / "processed.lstar.zarr"))


def test_search_root_escape_safe_and_absent(tmp_path):
    assert runs._search_root_for(str(tmp_path), "../../etc/passwd") is None
    assert runs._search_root_for(str(tmp_path), "nope") is None
    assert runs._search_root_for(None, "x") is None


def test_search_root_does_not_descend_matched_dir(tmp_path):
    store = tmp_path / "s.lstar.zarr"; store.mkdir()
    for i in range(5):
        (store / str(i)).write_text("c")     # chunks under the matched store must not be walked
    assert runs._search_root_for(str(tmp_path), "s.lstar.zarr") == os.path.realpath(str(store))


def test_resolve_run_output_path_retained_tier(tmp_path, monkeypatch):
    import core.compute.retention as retmod
    ret_loc = tmp_path / "retained"; (ret_loc / "out").mkdir(parents=True)
    (ret_loc / "out" / "keep.h5ad").write_text("data")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{"state": "done"}])
    monkeypatch.setattr(retmod, "location_path", lambda row: str(ret_loc))
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": None})
    assert runs.resolve_run_output_path("r", "keep.h5ad") == \
        os.path.realpath(str(ret_loc / "out" / "keep.h5ad"))


def test_resolve_run_output_path_jobdir_tier_zarr(tmp_path, monkeypatch):
    """The user's case: a fresh `.lstar.zarr` STORE in the live weft jobdir, not yet retained."""
    import core.compute.retention as retmod
    jd = tmp_path / "jobdir"; store = jd / "processed.lstar.zarr"; store.mkdir(parents=True)
    (store / "0").write_text("c")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [str(jd)])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": None})
    hit = runs.resolve_run_output_path("r", "processed.lstar.zarr")
    assert hit == os.path.realpath(str(store)) and os.path.isdir(hit)


def test_resolve_run_output_path_none_when_absent(tmp_path, monkeypatch):
    import core.compute.retention as retmod
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    monkeypatch.setattr(runs, "_run_jobdirs", lambda rid: [])
    monkeypatch.setattr(runs, "get_entity", lambda rid: {"artifact_path": str(tmp_path)})
    assert runs.resolve_run_output_path("r", "ghost.zarr") is None


def test_resolve_project_run_output_newest_first(monkeypatch):
    # list_entities is created_at ASC; resolve_project_run_output reverses → newest first
    monkeypatch.setattr(runs, "list_entities", lambda **kw: [{"id": "old"}, {"id": "new"}])
    seen = []
    def _r(rid, name):
        seen.append(rid)
        return "/abs/x.zarr" if rid == "new" else None
    monkeypatch.setattr(runs, "resolve_run_output_path", _r)
    assert runs.resolve_project_run_output("x.zarr") == ("new", "/abs/x.zarr")
    assert seen[0] == "new"


def test_run_jobdirs_maps_local_kernels_only(monkeypatch):
    monkeypatch.setattr(runs, "get_entity",
                        lambda rid: {"metadata": {"weft_targets": ["krn_a", "krn_remote"]}})
    import core.compute.adapter as admod
    import core.compute as compute
    monkeypatch.setattr(admod, "weft_workspace", lambda: Path("/ws"))

    class _W:
        def sync_call(self, name, *a):
            return {"kernels": [
                {"kernel_id": "krn_a", "site": "local", "jobdir": "kernels/krn_a"},
                {"kernel_id": "krn_remote", "site": "hpc", "jobdir": "kernels/krn_r"}]}
    monkeypatch.setattr(compute, "get_compute", lambda: _W())
    assert runs._run_jobdirs("r") == ["/ws/site-local/kernels/krn_a"]   # local only


_TESTS = [
    test_search_root_finds_file,
    test_search_root_finds_directory_store,
    test_search_root_prefers_dir_over_file,
    test_search_root_exact_child_wins,
    test_search_root_escape_safe_and_absent,
    test_search_root_does_not_descend_matched_dir,
    test_resolve_run_output_path_retained_tier,
    test_resolve_run_output_path_jobdir_tier_zarr,
    test_resolve_run_output_path_none_when_absent,
    test_resolve_project_run_output_newest_first,
    test_run_jobdirs_maps_local_kernels_only,
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
        sig = inspect.signature(t).parameters
        if "tmp_path" in sig:
            kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="vwr_"))
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
