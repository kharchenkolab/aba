"""zip_store_dir (core/viewers/store_serve.py) — packs a .lstar.zarr store DIR
into a zip that unpacks to a `<name>.lstar.zarr/` FOLDER (the download deliverable
= the regular directory store, not lstar's single-file STORED format). See
misc/pagoda3_integration.md."""
import zipfile
from pathlib import Path

from core.viewers.store_serve import zip_store_dir


def _make_store(root: Path) -> Path:
    store = root / "sample-abcd1234.lstar.zarr"
    (store / "axes").mkdir(parents=True)
    (store / ".zattrs").write_text('{"axes": ["cells", "genes"]}')
    (store / ".zgroup").write_text('{"zarr_format": 2}')
    (store / "axes" / ".zarray").write_text('{"shape": [10]}')
    (store / "axes" / "0").write_bytes(b"\x00" * 256)
    return store


def test_unpacks_to_a_named_folder(tmp_path):
    store = _make_store(tmp_path)
    out = zip_store_dir(store, tmp_path / "out.zip", "sample.lstar.zarr")
    assert out.is_file()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # every entry nested under the clean folder name → unzips to a directory
        assert all(n.startswith("sample.lstar.zarr/") for n in names), names
        assert "sample.lstar.zarr/.zattrs" in names
        assert "sample.lstar.zarr/axes/.zarray" in names
        assert "sample.lstar.zarr/axes/0" in names
        # content round-trips
        assert z.read("sample.lstar.zarr/.zattrs") == b'{"axes": ["cells", "genes"]}'


def test_is_deflate_compressed(tmp_path):
    # DEFLATE (a transport container the user unpacks), not the STORED single-file
    # hosting format — the point of switching to the regular directory store.
    store = _make_store(tmp_path)
    out = zip_store_dir(store, tmp_path / "out.zip", "sample.lstar.zarr")
    with zipfile.ZipFile(out) as z:
        assert all(i.compress_type == zipfile.ZIP_DEFLATED for i in z.infolist())


def test_extraction_yields_a_working_directory_store(tmp_path):
    store = _make_store(tmp_path)
    out = zip_store_dir(store, tmp_path / "out.zip", "sample.lstar.zarr")
    dest = tmp_path / "unpacked"
    with zipfile.ZipFile(out) as z:
        z.extractall(dest)
    d = dest / "sample.lstar.zarr"
    assert d.is_dir()
    assert (d / ".zattrs").is_file() and (d / "axes" / "0").is_file()


def test_deterministic(tmp_path):
    store = _make_store(tmp_path)
    a = zip_store_dir(store, tmp_path / "a.zip", "sample.lstar.zarr").read_bytes()
    b = zip_store_dir(store, tmp_path / "b.zip", "sample.lstar.zarr").read_bytes()
    assert a == b
