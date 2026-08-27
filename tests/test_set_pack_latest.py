"""`latest` may only be repointed at something that can actually be adopted.

CONTEXT. The pointer is normally written by `env_publish`, which builds an
image. Correcting it for an ALREADY-published version had no supported path, so
the fallback was editing catalog.json by hand — tried on 2026-08-27, and it made
a bad situation worse. `scripts/set_pack_latest.py` is that operation done
safely; these are the refusals that make it safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from set_pack_latest import repoint  # noqa: E402


def _store(tmp_path: Path, *, with_images=("aaa", "bbb")) -> Path:
    tree = tmp_path / "store"
    (tree / "envs").mkdir(parents=True)
    for h in with_images:
        d = tree / "envs" / h
        d.mkdir()
        (d / "image.sqfs").write_bytes(b"squashfs")
    (tree / "catalog.json").write_text(json.dumps({
        "catalog_version": 1,
        "envs": {
            "pack-a": {
                "latest": "2026.08.25-old",
                "versions": {
                    "2026.08.25-old": {"env_id": "env:v1:aaa"},
                    "2026.08.27-new": {"env_id": "env:v1:bbb"},
                },
            },
            "pack-b": {"latest": "2026.01.01-x",
                       "versions": {"2026.01.01-x": {"env_id": "env:v1:aaa"}}},
        },
    }, indent=1))
    return tree


def test_repoints_to_a_named_version(tmp_path):
    tree = _store(tmp_path)
    r = repoint(tree, "pack-a", "2026.08.27-new", False)
    assert r["changed"] and r["to"] == "2026.08.27-new"
    cat = json.loads((tree / "catalog.json").read_text())
    assert cat["envs"]["pack-a"]["latest"] == "2026.08.27-new"


def test_newest_picks_the_highest_version_string(tmp_path):
    tree = _store(tmp_path)
    r = repoint(tree, "pack-a", None, True)
    assert r["to"] == "2026.08.27-new"


def test_refuses_a_version_that_is_not_published(tmp_path):
    tree = _store(tmp_path)
    with pytest.raises(ValueError, match="not published"):
        repoint(tree, "pack-a", "2026.09.09-nope", False)
    assert json.loads((tree / "catalog.json").read_text(
    ))["envs"]["pack-a"]["latest"] == "2026.08.25-old", "catalog was modified anyway"


def test_refuses_a_version_whose_image_is_missing(tmp_path):
    """THE load-bearing refusal. A dangling `latest` is worse than a stale one:
    every consumer riding it fails to ADOPT, rather than merely running
    something older. This is the exact shape a pack mirrored as metadata
    without its bytes would take."""
    tree = _store(tmp_path, with_images=("aaa",))      # 'bbb' has no image
    with pytest.raises(ValueError, match="no image"):
        repoint(tree, "pack-a", "2026.08.27-new", False)
    assert json.loads((tree / "catalog.json").read_text(
    ))["envs"]["pack-a"]["latest"] == "2026.08.25-old"


def test_refuses_an_unknown_pack(tmp_path):
    tree = _store(tmp_path)
    with pytest.raises(ValueError, match="not in"):
        repoint(tree, "pack-z", "x", False)


def test_is_idempotent(tmp_path):
    tree = _store(tmp_path)
    repoint(tree, "pack-a", "2026.08.27-new", False)
    r = repoint(tree, "pack-a", "2026.08.27-new", False)
    assert r["changed"] is False


def test_leaves_every_other_pack_untouched(tmp_path):
    """WIDE: the store is shared. Repointing one pack must not perturb another,
    or a repair becomes its own outage."""
    tree = _store(tmp_path)
    before = json.loads((tree / "catalog.json").read_text())["envs"]["pack-b"]
    repoint(tree, "pack-a", "2026.08.27-new", False)
    after = json.loads((tree / "catalog.json").read_text())["envs"]["pack-b"]
    assert before == after


def test_the_result_is_still_valid_json_with_the_versions_intact(tmp_path):
    """The catalog is read by every consumer at any moment. A repair that
    truncates or reshapes it is the worst possible outcome."""
    tree = _store(tmp_path)
    repoint(tree, "pack-a", "2026.08.27-new", False)
    cat = json.loads((tree / "catalog.json").read_text())
    assert set(cat["envs"]["pack-a"]["versions"]) == {
        "2026.08.25-old", "2026.08.27-new"}, "a version disappeared"
    assert cat.get("catalog_version") == 1, "top-level fields lost"
    assert not list(tree.glob("catalog.json.tmp*")), "temp file left behind"
