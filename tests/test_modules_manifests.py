"""Modules Phase 1 (misc/modules.md): the catalog is loaded from manifests
(install/core/modules/*.yaml), not hardcoded. PARITY — the loaded specs must equal the
prior hardcoded values, and every install_script must resolve to a real file.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.modules.registry as reg          # noqa: E402


def test_catalog_order_and_ids():
    reg.reload()
    assert reg.ids() == ("python-bio", "r-bio", "viewer-pagoda3")   # by `order`


def test_manifest_parity_with_prior_catalog():
    reg.reload()
    d = {m.id: m for m in reg.all_modules()}
    assert d["python-bio"].default_state == "on"
    assert d["python-bio"].removable is False and d["python-bio"].env_target == "base-update"
    assert "scanpy" in d["python-bio"].first_use
    assert d["r-bio"].default_state == "first_use" and d["r-bio"].removable is True
    assert d["r-bio"].env_target == "conda-tools" and "seurat" in d["r-bio"].first_use
    assert d["viewer-pagoda3"].default_state == "first_use" and d["viewer-pagoda3"].removable is True
    assert ".lstar.zarr" in d["viewer-pagoda3"].first_use


def test_install_scripts_are_absolute_and_exist():
    reg.reload()
    for m in reg.all_modules():
        p = Path(m.install_script)
        assert p.is_absolute(), f"{m.id}: install_script not absolute: {m.install_script}"
        assert p.is_file(), f"{m.id}: install script missing: {p}"
        assert p.name == f"install-{m.id}.sh"


def test_probe_and_remove_declared():
    reg.reload()
    d = {m.id: m for m in reg.all_modules()}
    assert d["python-bio"].ready == {"base_stage": "ready"}
    assert d["r-bio"].ready == {"r_package": {"env": "tools", "package": "Seurat"}}
    assert d["r-bio"].remove.get("paths") == ["$TOOLS_ENV"]
    assert d["viewer-pagoda3"].ready == {"path_exists": "$PAGODA3_DIST/index.html"}
    assert d["viewer-pagoda3"].remove.get("paths") == ["$PAGODA3_DIST"]
    assert d["python-bio"].remove == {}      # not removable → no remove block
