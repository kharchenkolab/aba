"""Capability-tool fixes surfaced by the pagoda2 install (P5 diagnosis):

  #1 propose_capability UPSERTS a project-scoped entry (so the agent can correct
     a wrong git ref it just proposed) instead of returning 'already_available'.
  #2 ensure_capability accepts ref/source/package overrides (tested at the
     validator/build layer; the actual R install needs a live env).
  #3 the 'conda' R source is accepted by the validator (r-hdf5r etc.), so
     conda-forge R binaries can be catalogued without an 'invalid characters'
     rejection.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio  # noqa: E402,F401  — registers the bio seed provider
from core.graph import _schema  # noqa: E402
from core.graph._schema import init_db  # noqa: E402
from core.exec.r import validate_install  # noqa: E402
from content.bio.tools.discovery import propose_capability_tool  # noqa: E402
from core.catalog import resolve_capability, register_capability  # noqa: E402

pytestmark = pytest.mark.bio


# ── #3: validator accepts conda coordinates, github rule intact ──────────────
def test_validate_install_conda_accepts_hyphenated_names():
    assert validate_install("conda", "r-hdf5r", None) is None
    assert validate_install("conda", "hdf5", None) is None
    assert "invalid characters" in (validate_install("conda", "bad name!", None) or "")


def test_validate_install_unknown_source_lists_conda():
    assert "conda" in (validate_install("nope", "x", None) or "")


def test_validate_install_github_still_requires_owner_repo():
    # the helpful rejection (not a bug) stays
    assert "owner/repo" in (validate_install("github", "pagoda2-devel", None) or "")
    assert validate_install("github", "kharchenkolab/pagoda2", "devel") is None


def test_cran_still_rejects_hyphen():
    # a real CRAN name has no hyphen — keep that strict
    assert "invalid characters" in (validate_install("cran", "r-hdf5r", None) or "")


# ── #1: upsert ──────────────────────────────────────────────────────────────
@pytest.fixture
def cat_db(tmp_path):
    tok = _schema.bind_active_db(str(tmp_path / "cap.db"))
    init_db()
    yield
    _schema.reset_active_db(tok)


def test_propose_updates_project_scoped_ref(cat_db):
    # propose a GitHub r_package with the WRONG ref
    r1 = propose_capability_tool({
        "name": "pagoda2-devel", "archetype": "r_package", "source": "github",
        "package": "kharchenkolab/pagoda2", "library": "pagoda2", "ref": "dev"})
    assert r1.get("status") == "approved", r1
    assert resolve_capability("pagoda2-devel")["provisioning"]["r"]["ref"] == "dev"

    # re-propose with the CORRECTED ref → UPDATE (not 'already_available')
    r2 = propose_capability_tool({
        "name": "pagoda2-devel", "archetype": "r_package", "source": "github",
        "package": "kharchenkolab/pagoda2", "library": "pagoda2", "ref": "devel"})
    assert r2.get("status") == "updated", r2
    assert resolve_capability("pagoda2-devel")["provisioning"]["r"]["ref"] == "devel"


def test_propose_does_not_clobber_curated_entry(cat_db):
    # a curated (system-scope) catalog entry must NOT be overwritten by a re-propose
    register_capability({"name": "curated-x", "archetype": "r_package", "scope": "system",
                         "provisioning": {"r": {"source": "cran", "package": "curated-x", "ref": None}}})
    r = propose_capability_tool({
        "name": "curated-x", "archetype": "r_package", "source": "github",
        "package": "owner/repo", "ref": "x"})
    assert r.get("status") == "already_available", r
    # unchanged
    assert resolve_capability("curated-x")["provisioning"]["r"]["source"] == "cran"


# ── #3: a conda-sourced r_package catalogs cleanly (build path) ──────────────
def test_propose_conda_r_package_builds_provisioning(cat_db):
    r = propose_capability_tool({
        "name": "hdf5r", "archetype": "r_package", "source": "conda",
        "package": "r-hdf5r"})   # library defaults: r-hdf5r → hdf5r
    assert r.get("status") == "approved", r
    prov = resolve_capability("hdf5r")["provisioning"]["r"]
    assert prov["source"] == "conda" and prov["package"] == "r-hdf5r"
    assert prov["library"] == "hdf5r"
