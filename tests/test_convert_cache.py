"""Derived-store cache (core/viewers/convert_cache.py): convert once, reuse,
re-derive on source or version change."""
from core.viewers.convert_cache import ensure_derived, source_sig


def _fake_convert_factory(counter):
    def convert(src, out):
        counter.append(1)
        out.mkdir(parents=True, exist_ok=True)
        (out / ".zmetadata").write_text(src.read_text())
    return convert


def test_builds_then_reuses(tmp_path):
    src = tmp_path / "a.h5ad"; src.write_text("v1")
    cache = tmp_path / "cache"
    calls = []
    conv = _fake_convert_factory(calls)
    p1 = ensure_derived(src, cache, "a.lstar.zarr", "v1", conv)
    assert (p1 / ".zmetadata").read_text() == "v1"
    p2 = ensure_derived(src, cache, "a.lstar.zarr", "v1", conv)
    assert p1 == p2
    assert len(calls) == 1                     # second call was a cache hit


def test_rederives_on_source_change(tmp_path):
    src = tmp_path / "a.h5ad"; src.write_text("v1")
    cache = tmp_path / "cache"
    calls = []
    conv = _fake_convert_factory(calls)
    ensure_derived(src, cache, "a.lstar.zarr", "v1", conv)
    src.write_text("v2-longer")               # size + mtime change
    out = ensure_derived(src, cache, "a.lstar.zarr", "v1", conv)
    assert (out / ".zmetadata").read_text() == "v2-longer"
    assert len(calls) == 2


def test_rederives_on_version_bump(tmp_path):
    src = tmp_path / "a.h5ad"; src.write_text("v1")
    cache = tmp_path / "cache"
    calls = []
    conv = _fake_convert_factory(calls)
    ensure_derived(src, cache, "a.lstar.zarr", "v1", conv)
    ensure_derived(src, cache, "a.lstar.zarr", "v2", conv)   # version changed
    assert len(calls) == 2


def test_crashed_build_not_cached(tmp_path):
    src = tmp_path / "a.h5ad"; src.write_text("v1")
    cache = tmp_path / "cache"

    def boom(src, out):
        out.mkdir(parents=True, exist_ok=True)
        raise RuntimeError("convert failed")

    try:
        ensure_derived(src, cache, "a.lstar.zarr", "v1", boom)
    except RuntimeError:
        pass
    # No valid cache meta → a subsequent good build runs (not treated as cached).
    calls = []
    ensure_derived(src, cache, "a.lstar.zarr", "v1", _fake_convert_factory(calls))
    assert len(calls) == 1
    assert (cache / "a.lstar.zarr" / ".zmetadata").exists()
