"""weft rewrite W1: the env-pack consumer (core/compute/env_packs) — resolve a
named pack from the envs/ facet to a weft EnvSpec/EnvID, and the capability
import-name recognition that keeps ensure_capability from re-installing what a
base already provides (§4b(i)).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.loader import EnvPack   # noqa: E402


class _FakeBundle:
    def __init__(self, packs):
        self.env_packs = packs


@pytest.fixture()
def packed(monkeypatch):
    packs = [
        EnvPack("python-bio", {
            "name": "python-bio", "title": "Single-cell Python",
            "languages": ["python"], "default_state": "on",
            "role": "base",
            "import_names": {"scvi": "scvi-tools"},
            "spec": {"platforms": ["linux-64"],
                     "deps": {"conda": ["python =3.12", "scanpy"]}},
        }, "system"),
        EnvPack("r-bio", {
            "name": "r-bio", "languages": ["r"], "default_state": "first_use",
            "spec": {"deps": {"conda": ["r-base =4.4", "r-seurat"]}},
        }, "system"),
    ]
    import core.bundle.active as active
    monkeypatch.setattr(active, "get_bundle", lambda: _FakeBundle(packs))
    from core.compute import env_packs
    return env_packs


def test_list_packs_renders_rows(packed):
    rows = {r["name"]: r for r in packed.list_packs()}
    assert rows["python-bio"]["default_state"] == "on"
    assert rows["python-bio"]["role"] == "base"
    assert rows["r-bio"]["languages"] == ["r"]


def test_pack_spec_is_verbatim_plus_name(packed):
    spec = packed.pack_spec("python-bio")
    assert spec["deps"] == {"conda": ["python =3.12", "scanpy"]}
    assert spec["platforms"] == ["linux-64"]
    assert spec["name"] == "python-bio"        # label carried, identity unchanged
    assert packed.pack_spec("nope") is None


def test_import_name_resolution(packed):
    assert packed.import_name_for("scvi") == "scvi-tools"   # aliased
    assert packed.import_name_for("unknown") is None


def test_packs_providing_recognizes_base_contents(packed):
    assert "python-bio" in packed.packs_providing("scanpy")     # in deps
    assert "python-bio" in packed.packs_providing("scvi")       # in import_names
    assert packed.packs_providing("torch") == []


def test_ensure_unknown_pack_raises(packed):
    import asyncio
    from core.compute.errors import ComputeError
    with pytest.raises(ComputeError) as ei:
        asyncio.run(packed.ensure_pack("does-not-exist"))
    assert ei.value.code == "unknown_pack"
    assert "python-bio" in ei.value.hints["available"]
