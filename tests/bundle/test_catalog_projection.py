"""The capability catalog is a PROJECTION of EffectiveBundle.catalog, composed
from each scope's catalog/ dir (system → installation → lab → user) — the exact
mirror of the skills seam (test_skill_projection.py).

Catalog CONTENT is pack-sourced: it lives in the recipe pack, imported into the
installation scope. The backend system scope vendors NONE of it. These tests
feed a fixture installation/lab catalog and assert the bundle composes it +
the bio seeder projects it into the live per-project catalog.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

# A minimal installation-scope catalog: two r_package capabilities + an R-base
# manifest. Filename-agnostic — the composer dispatches on `capabilities:` vs
# `packages:`, so this stands in for the pack's python_bio/r_bioconductor yamls.
_SEED = (
    "capabilities:\n"
    "  - name: DESeq2\n"
    "    version: latest\n"
    "    archetype: r_package\n"
    "    provisioning: {r: {source: bioconductor, package: DESeq2, library: DESeq2}}\n"
    "  - name: pagoda2\n"
    "    version: latest\n"
    "    archetype: r_package\n"
    "    provisioning: {r: {source: cran, package: pagoda2, library: pagoda2}}\n"
    "packages:\n"
    "  - r-seurat\n"
    "  - bioconductor-deseq2\n"
)


def _reload():
    from core.bundle.active import reload_bundle
    return reload_bundle()


def _installation(tmp_path, seed=_SEED):
    b = tmp_path / "installation"
    (b / "catalog").mkdir(parents=True)
    (b / "catalog" / "seed.yaml").write_text(seed)
    return b


@pytest.fixture(autouse=True)
def _restore_system_only():
    yield
    _reload()


def test_system_scope_ships_no_catalog():
    """The backend system scope vendors no catalog — content is pack-sourced."""
    eb = _reload()
    assert eb.catalog == []
    assert eb.r_base_specs == []
    assert eb.collection_dirs == []


def test_installation_catalog_composes(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(_installation(tmp_path)))
    eb = _reload()
    by = {c.name: c for c in eb.catalog}
    assert "DESeq2" in by and by["DESeq2"].source_scope == "institution"
    assert "pagoda2" in by
    assert {"r-seurat", "bioconductor-deseq2"} <= set(eb.r_base_specs)


def test_lab_overrides_installation_and_extends_rbase(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(_installation(tmp_path)))
    lab = tmp_path / "lab"
    (lab / "catalog").mkdir(parents=True)
    (lab / "AGENTS.md").write_text("Lab policy\n")
    (lab / "catalog" / "override.yaml").write_text(
        "capabilities:\n"
        "  - name: DESeq2\n"
        "    version: lab\n"
        "    archetype: r_package\n"
        "    summary: LAB DESeq2\n"
        "    provisioning: {r: {source: github, package: lab/DESeq2, library: DESeq2}}\n"
        "packages:\n"
        "  - r-lab-extra\n")
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(lab))
    monkeypatch.setenv("ABA_GROUP", "testlab")
    eb = _reload()
    deseq = next(c for c in eb.catalog if c.name == "DESeq2")
    assert deseq.source_scope == "lab"                       # narrowest wins
    assert deseq.spec["summary"] == "LAB DESeq2"
    assert eb.provenance.capabilities["DESeq2"]["shadowed_in"] == ["institution"]
    # R-base EXTENDS across scopes (it doesn't override).
    assert {"r-seurat", "r-lab-extra"} <= set(eb.r_base_specs)


def test_seeder_projects_into_live_catalog(tmp_path, monkeypatch):
    """The runtime path: a catalog query lazily seeds the bundle's catalog into
    the active project DB; resolve_capability then finds it."""
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(_installation(tmp_path)))
    _reload()
    from core.graph._schema import init_db, bind_active_db, reset_active_db
    tok = bind_active_db(tmp_path / "cat.db")
    try:
        init_db()
        from core.catalog import list_capabilities, resolve_capability
        names = {c["name"] for c in list_capabilities()}     # triggers lazy seed
        assert {"DESeq2", "pagoda2"} <= names
        cap = resolve_capability("DESeq2")
        assert cap and (cap.get("provisioning") or {}).get("r", {}).get("source") == "bioconductor"
    finally:
        reset_active_db(tok)


def test_load_r_base_specs_reads_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(_installation(tmp_path)))
    _reload()
    import content.bio.capabilities as caps
    assert "r-seurat" in caps.load_r_base_specs()


def test_collection_dir_registers(tmp_path, monkeypatch):
    inst = _installation(tmp_path)
    coll = inst / "catalog" / "mycoll"
    coll.mkdir(parents=True)
    (coll / "collection.yaml").write_text(
        "name: mycoll\nkind: reference\nscope: institution\nindex: index.json\n")
    (coll / "index.json").write_text(
        '[{"name": "mycoll-tool", "domain": "x", "summary": "a lab reference tool"}]')
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(inst))
    eb = _reload()
    assert any(d.name == "mycoll" for d in eb.collection_dirs)
