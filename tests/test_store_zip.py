"""zip_store_stored (core/viewers/store_serve.py) — packs a .lstar.zarr store
DIR into a single STORED (uncompressed, range-readable) .lstar.zarr.zip for the
'quick download' path. See misc/pagoda3_integration.md."""
import zipfile
from pathlib import Path

from core.viewers.store_serve import zip_store_stored


def _make_store(root: Path) -> Path:
    store = root / "sample.lstar.zarr"
    (store / "axes").mkdir(parents=True)
    # dotfile metadata (must be included) + a nested chunk
    (store / ".zattrs").write_text('{"axes": ["cells", "genes"]}')
    (store / ".zgroup").write_text('{"zarr_format": 2}')
    (store / "axes" / ".zarray").write_text('{"shape": [10]}')
    (store / "axes" / "0").write_bytes(b"\x00" * 128)
    return store


def test_zip_is_stored_and_has_top_level_arcnames(tmp_path):
    store = _make_store(tmp_path)
    out = zip_store_stored(store, tmp_path / "out.lstar.zarr.zip")
    assert out.is_file()
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        # archive root IS the store root — dotfiles at top level (matches _unzip_store)
        assert ".zattrs" in names
        assert ".zgroup" in names
        assert "axes/.zarray" in names
        assert "axes/0" in names
        # STORED, not DEFLATE → range-readable
        for info in z.infolist():
            assert info.compress_type == zipfile.ZIP_STORED
        # content round-trips
        assert z.read(".zattrs") == b'{"axes": ["cells", "genes"]}'


def test_zip_is_deterministic(tmp_path):
    store = _make_store(tmp_path)
    a = zip_store_stored(store, tmp_path / "a.zip").read_bytes()
    b = zip_store_stored(store, tmp_path / "b.zip").read_bytes()
    assert a == b            # sorted walk → byte-stable archive
