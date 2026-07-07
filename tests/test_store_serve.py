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


# ── extra_roots: follow a project-internal symlink (serve a store in place) ──
def test_extra_root_symlink_followed(tmp_path):
    # pagoda3/<name> → work/<ana>/<name>, both inside the project → served in place.
    proj = tmp_path / "proj"
    base = proj / "pagoda3"; base.mkdir(parents=True)
    store = proj / "work" / "ana" / "x.lstar.zarr"; store.mkdir(parents=True)
    (store / ".zattrs").write_text("{}")
    (base / "x-abcd.lstar.zarr").symlink_to(store, target_is_directory=True)
    f = resolve_within(base, "x-abcd.lstar.zarr/.zattrs", extra_roots=(proj,))
    assert f == (store / ".zattrs").resolve()   # no copy — the real work/ file


def test_extra_root_does_not_reopen_dotdot(tmp_path):
    # Widening the allowed roots must NOT let a `..` URL walk out of the store dir.
    proj = tmp_path / "proj"
    base = proj / "pagoda3"; base.mkdir(parents=True)
    (proj / "secret").write_text("x")
    with pytest.raises(ValueError):
        resolve_within(base, "../secret", extra_roots=(proj,))


def test_symlink_out_of_project_still_rejected(tmp_path):
    # A link pointing clean out of the project is rejected even with extra_roots.
    proj = tmp_path / "proj"
    base = proj / "pagoda3"; base.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "f").write_text("x")
    (base / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_within(base, "link/f", extra_roots=(proj,))
