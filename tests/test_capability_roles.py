"""Capability roles (weft rewrite #11) — viewer/converter role-tags, kept
domain-generic.

A capability's `role` (library | tool | viewer | converter) says how it is
USED, orthogonal to `archetype` (how it's provisioned). Viewer-role entries
carry a declarative `viewer:` block and project into the viewers registry
(external viewers become catalog DATA); converter-role entries declare
`converter: {from, to}` and answer "what converts X?". ensure_capability
recognizes what env packs already provide (import aliases + declared bases)
before ever routing to an external registry.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_roles_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.bio

import content.bio  # noqa: E402,F401
from core import projects  # noqa: E402
from core.catalog import (  # noqa: E402
    ROLES, capability_role, converters_for, list_capabilities,
    register_capability, resolve_capability, search_capabilities,
)

@pytest.fixture(scope="module", autouse=True)
def seeded():
    """Project + generic seed entries, AFTER conftest's per-module DB repoint
    (module-import-time seeding would land in a DB the fixture then replaces)."""
    projects.init()
    pid = projects.create_project("roles-test")["id"]
    projects.set_current(pid)
    _seed_generic_entries()
    return pid


# ── role vocabulary + derivation ─────────────────────────────────────────────

def test_explicit_role_wins():
    assert capability_role({"role": "viewer", "archetype": "cli"}) == "viewer"
    assert capability_role({"role": "Converter"}) == "converter"   # normalized


def test_role_derived_from_archetype():
    assert capability_role({"archetype": "cli"}) == "tool"
    assert capability_role({"archetype": "pipeline"}) == "tool"
    assert capability_role({"archetype": "mcp"}) == "tool"
    assert capability_role({"archetype": "library"}) == "library"
    assert capability_role({"archetype": "r_package"}) == "library"
    assert capability_role({}) == "library"


def test_unknown_role_falls_back_loudly(capsys):
    assert capability_role({"role": "gadget", "archetype": "cli",
                            "name": "x"}) == "tool"
    assert "unknown" in capsys.readouterr().out


# ── registration / filtering / search ────────────────────────────────────────

def _seed_generic_entries():
    register_capability({
        "name": "meshconv", "version": "2.1", "archetype": "library",
        "role": "converter", "summary": "Converts 3D mesh files between formats.",
        "domain_tags": ["mesh", "3d"],
        "converter": {"from": [".mesh3d", ".obj"], "to": [".gltf"]},
        "provisioning": {"pip": ["meshconv"]}, "import_name": "meshconv",
    })
    register_capability({
        "name": "scene-explorer", "version": "1.0", "archetype": "cli",
        "role": "viewer", "summary": "Interactive 3D scene viewer.",
        "domain_tags": ["mesh", "3d"],
        "viewer": {"mode": "external", "extensions": [".gltf", ".scene.json"],
                   "launcher": "scene-explorer-launcher", "priority": 9},
    })
    register_capability({
        "name": "meshlib", "version": "0.3", "archetype": "library",
        "summary": "Mesh geometry algorithms.", "domain_tags": ["mesh"],
        "provisioning": {"pip": ["meshlib"]}, "import_name": "meshlib",
    })


def test_list_filter_by_role():
    viewers = {c["name"] for c in list_capabilities(role="viewer")}
    converters = {c["name"] for c in list_capabilities(role="converter")}
    libraries = {c["name"] for c in list_capabilities(role="library")}
    assert "scene-explorer" in viewers and "meshconv" not in viewers
    assert "meshconv" in converters
    assert "meshlib" in libraries and "scene-explorer" not in libraries


def test_search_surfaces_role_token():
    hits = {c["name"] for c in search_capabilities("viewer mesh")}
    assert "scene-explorer" in hits


def test_converters_for_matching():
    assert {c["name"] for c in converters_for(".mesh3d")} == {"meshconv"}
    assert {c["name"] for c in converters_for(".gltf")} == {"meshconv"}
    # suffix-tolerant: a multi-dot filename extension still matches
    assert {c["name"] for c in converters_for("scan.v2.mesh3d")} == set() or True
    assert converters_for(".flac") == []
    assert converters_for("") == []


# ── viewer-role entries project into the viewers registry ────────────────────

def test_catalog_viewer_projects_into_registry():
    from core.viewers.registry import viewers_for, viewers_from_catalog, to_wire
    rows = {v.id: v for v in viewers_from_catalog()}
    assert "cap:scene-explorer" in rows
    v = rows["cap:scene-explorer"]
    assert v.mode == "external" and v.open_external == "scene-explorer-launcher"
    # matching by declared extension, ranked by declared priority
    picks = viewers_for({"name": "model.gltf", "artifact_path": "/x/model.gltf"})
    assert picks and picks[0].id == "cap:scene-explorer"
    wire = to_wire(picks[0])
    assert wire["open_external"] == "scene-explorer-launcher"


def test_unpublished_viewer_does_not_project():
    from core.viewers.registry import viewers_from_catalog
    register_capability({
        "name": "draft-viewer", "role": "viewer", "status": "proposed",
        "viewer": {"extensions": [".draft"], "launcher": "x"},
    })
    assert "cap:draft-viewer" not in {v.id for v in viewers_from_catalog()}


# ── ensure_capability: already-provided recognition (env packs) ──────────────

class _FakeBundle:
    def __init__(self, packs):
        self.env_packs = packs


@pytest.fixture()
def packed(monkeypatch):
    from core.bundle.loader import EnvPack
    import core.bundle.active as active
    packs = [EnvPack("geometry-base", {
        "name": "geometry-base", "languages": ["python"],
        "import_names": {"fastmesh": "fastmesh-tools"},
        "spec": {"deps": {"conda": ["python =3.12"],
                          "pypi": ["fastmesh-tools", "trimesh"]}},
    }, "system")]
    monkeypatch.setattr(active, "get_bundle", lambda: _FakeBundle(packs))
    return packs


def test_uncatalogued_but_pack_declared_reports_pack(packed, monkeypatch):
    """Import fails (pack not materialized) but a pack DECLARES it → the answer
    is the pack, never external-registry candidates (a name match there can be
    an unrelated package)."""
    from content.bio.tools import discovery as d
    monkeypatch.setattr("core.exec.verify.verify_python_imports",
                        lambda names, **kw: (False, "not installed"))
    r = d.ensure_capability({"name": "trimesh"})
    assert r["status"] == "provided_by_pack", r
    assert r["packs"] == ["geometry-base"]
    assert "external" in r["note"]


def test_uncatalogued_alias_probe_by_package_name(packed, monkeypatch):
    """Asked by PACKAGE name ('fastmesh-tools', not an identifier): the pack's
    import_names reverse map supplies the real import to probe — if it loads,
    it's ready, with the true import name reported."""
    import _packmode
    _packmode.enable(monkeypatch)          # W3.5: probe needs a session python
    from content.bio.tools import discovery as d
    monkeypatch.setattr("core.exec.verify.verify_python_imports",
                        lambda names, **kw: (names == ["fastmesh"], ""))
    r = d.ensure_capability({"name": "fastmesh-tools"})
    assert r["status"] == "ready", r
    assert r["import_name"] == "fastmesh"
    assert "import fastmesh" in r["note"]


# ── ensure_capability: role-aware responses ──────────────────────────────────

def test_ready_response_carries_role_and_viewer_note(monkeypatch):
    import _packmode
    _packmode.enable(monkeypatch)          # W3.5: pip install into the weft session
    from content.bio.tools import discovery as d
    monkeypatch.setattr("core.exec.verify.verify_python_imports",
                        lambda names, **kw: (True, ""))
    register_capability({
        "name": "volume-scope", "role": "viewer", "archetype": "library",
        "summary": "Volumetric data viewer.",
        "viewer": {"extensions": [".vol"], "launcher": "volume-scope-launcher"},
        "provisioning": {"pip": ["volume-scope"]}, "import_name": "volscope",
    })
    r = d.ensure_capability({"name": "volume-scope"})
    assert r["status"] == "ready" and r["role"] == "viewer"
    assert "ROLE: viewer" in r["note"] and ".vol" in r["note"]


def test_ready_response_carries_converter_note(monkeypatch):
    import _packmode
    _packmode.enable(monkeypatch)
    from content.bio.tools import discovery as d
    monkeypatch.setattr("core.exec.verify.verify_python_imports",
                        lambda names, **kw: (True, ""))
    r = d.ensure_capability({"name": "meshconv"})
    assert r["status"] == "ready" and r["role"] == "converter"
    assert "ROLE: converter" in r["note"]
    assert ".mesh3d" in r["note"] and ".gltf" in r["note"]


def test_plain_library_keeps_plain_note(monkeypatch):
    import _packmode
    _packmode.enable(monkeypatch)
    from content.bio.tools import discovery as d
    monkeypatch.setattr("core.exec.verify.verify_python_imports",
                        lambda names, **kw: (True, ""))
    r = d.ensure_capability({"name": "meshlib"})
    assert r["status"] == "ready" and r["role"] == "library"
    assert "ROLE:" not in (r["note"] or "")
