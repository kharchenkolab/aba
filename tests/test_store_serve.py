"""Containment for external-viewer store serving (core/viewers/store_serve.py).
Security-critical: a client-supplied store path must not escape the root."""
import pytest

from core.viewers.store_serve import resolve_within


def test_normal_path_resolves(tmp_path):
    (tmp_path / "s.lstar.zarr").mkdir()
    (tmp_path / "s.lstar.zarr" / ".zmetadata").write_text("{}")
    f = resolve_within(tmp_path, "s.lstar.zarr/.zmetadata")
    assert f == (tmp_path / "s.lstar.zarr" / ".zmetadata").resolve()


def test_empty_relpath_is_base(tmp_path):
    assert resolve_within(tmp_path, "") == tmp_path.resolve()


def test_nested_chunk_path(tmp_path):
    f = resolve_within(tmp_path, "s.lstar.zarr/fields/counts/c/0/0")
    assert str(f).endswith("s.lstar.zarr/fields/counts/c/0/0")


@pytest.mark.parametrize("evil", [
    "../secret",
    "s/../../etc/passwd",
    "../../..",
])
def test_traversal_rejected(tmp_path, evil):
    with pytest.raises(ValueError):
        resolve_within(tmp_path, evil)


def test_absolute_path_neutralized_to_in_base(tmp_path):
    # A leading "/" is stripped, so an absolute-looking path can't escape —
    # it's treated as store-relative (and simply 404s if nonexistent).
    f = resolve_within(tmp_path, "/etc/passwd")
    assert f == (tmp_path / "etc" / "passwd").resolve()


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        resolve_within(tmp_path, "link")
