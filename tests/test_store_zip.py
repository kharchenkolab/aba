"""zip_store_stored (core/viewers/store_serve.py) — the generic FALLBACK packer
for the download deliverable: a STORED single-file `.lstar.zarr.zip` (contents at
the zip root). The pagoda3 launcher normally delegates to lstar's own packer; this
guards the equivalent core fallback. See misc/pagoda3_integration.md."""
import zipfile
from pathlib import Path

from core.viewers.store_serve import zip_store_stored


def _make_store(root: Path) -> Path:
    store = root / "sample-abcd1234.lstar.zarr"
    (store / "axes").mkdir(parents=True)
    (store / ".zattrs").write_text('{"axes": ["cells", "genes"]}')
    (store / ".zgroup").write_text('{"zarr_format": 2}')
    (store / "axes" / ".zarray").write_text('{"shape": [10]}')
    (store / "axes" / "0").write_bytes(b"\x00" * 256)
    return store


def test_single_file_stored_contents_at_root(tmp_path):
    store = _make_store(tmp_path)
    out = zip_store_stored(store, tmp_path / "out.lstar.zarr.zip")
    assert out.is_file()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # single-file store: metadata + chunks at the ROOT (not nested in a folder)
        assert ".zattrs" in names and "axes/.zarray" in names and "axes/0" in names
        assert not any(n.startswith("sample") for n in names)
        # metadata (.z*) first so a range reader hits the manifest early
        import os as _os
        assert _os.path.basename(z.infolist()[0].filename).startswith(".z")
        assert z.read(".zattrs") == b'{"axes": ["cells", "genes"]}'


def test_all_stored_not_deflate(tmp_path):
    # STORED keeps entries byte-range-readable inside the single file (the point
    # of the .lstar.zarr.zip format); a DEFLATE entry would defeat range reads.
    store = _make_store(tmp_path)
    out = zip_store_stored(store, tmp_path / "out.lstar.zarr.zip")
    with zipfile.ZipFile(out) as z:
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())


def test_deterministic(tmp_path):
    store = _make_store(tmp_path)
    a = zip_store_stored(store, tmp_path / "a.zip").read_bytes()
    b = zip_store_stored(store, tmp_path / "b.zip").read_bytes()
    assert a == b
