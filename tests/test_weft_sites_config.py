"""W3.1 (weft rewrite): deployment-declared weft sites + published-catalog
adoption plumbing — the fast tier (no docker; the cluster round trip lives in
test_weft_cluster.py, opt-in).
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_sites_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
os.environ["ABA_WEFT_WORKSPACE"] = str(Path(_tmp) / "weft-ws")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

weft_ok = False
try:
    from core.compute import adapter as _ad
    weft_ok = _ad.resolve_pixi() is not None
except Exception:  # noqa: BLE001
    pass


@pytest.fixture(scope="module", autouse=True)
def _teardown():
    yield
    try:
        _ad.shutdown()
        _ad._status = {"ok": False, "severity": "info", "detail": "torn down by test"}
    except Exception:  # noqa: BLE001
        pass


def test_sites_config_path_defaults_under_home():
    from core.compute.adapter import sites_config_path
    assert sites_config_path() == Path(os.environ["ABA_HOME"]) / "weft-sites.yaml"


@pytest.mark.skipif(not weft_ok, reason="weft/pixi unavailable")
def test_declared_local_site_registers_at_configure(monkeypatch):
    """A weft-sites.yaml entry becomes a registered site at configure() — the
    generic path the cluster test exercises with kind=slurm."""
    home = Path(os.environ["ABA_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "weft-sites.yaml").write_text(
        "sites:\n"
        f"  - name: scratch2\n    kind: local\n"
        f"    config: {{root: {_tmp}/site2-root}}\n")
    monkeypatch.setattr(_ad, "_adapter", None)
    st = _ad.configure()
    assert st["ok"], st["detail"]
    names = {s["name"] for s in _ad.get_compute().sync_call("sites_list")}
    assert {"local", "scratch2"} <= names


@pytest.mark.skipif(not weft_ok, reason="weft/pixi unavailable")
def test_malformed_sites_config_never_blocks_boot(monkeypatch, capsys):
    home = Path(os.environ["ABA_HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "weft-sites.yaml").write_text("sites: [ {name: broken")   # bad yaml
    _ad.shutdown()
    monkeypatch.setattr(_ad, "_adapter", None)
    st = _ad.configure()
    assert st["ok"], st["detail"]                # boot survives, loudly
    assert "unreadable sites config" in capsys.readouterr().out


# ── adoption plumbing (stubbed substrate) ────────────────────────────────────

def test_adopt_env_id_none_without_catalog(monkeypatch):
    monkeypatch.delenv("ABA_WEFT_PUBLISH_TREE", raising=False)
    from core.compute import seeding
    assert seeding.adopt_env_id("any-pack") is None


def test_adopt_env_id_uses_catalog(monkeypatch):
    monkeypatch.setenv("ABA_WEFT_PUBLISH_TREE", "/shared/envs")
    monkeypatch.setenv("ABA_WEFT_PUBLISH_SITE", "hpc")

    class _Stub:
        async def env_adopt(self, site, tree, name, **kw):
            assert (site, tree, name) == ("hpc", "/shared/envs", "geo-base")
            return {"env_id": "env:v1:published"}
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: _Stub())
    from core.compute import seeding
    assert seeding.adopt_env_id("geo-base") == "env:v1:published"


def test_adopt_miss_is_loud_and_falls_back(monkeypatch, capsys):
    monkeypatch.setenv("ABA_WEFT_PUBLISH_TREE", "/shared/envs")
    from core.compute.errors import ComputeError

    class _Stub:
        async def env_adopt(self, *a, **kw):
            raise ComputeError("env.unsatisfiable_on_site", "not in catalog",
                               stage="solve")
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: _Stub())
    from core.compute import seeding
    assert seeding.adopt_env_id("geo-base") is None
    assert "MISSED" in capsys.readouterr().out


def test_base_env_prefers_adoption(monkeypatch):
    """base_env.env_id: catalog adoption wins; private solve only on a miss."""
    from core.bundle.loader import EnvPack
    import core.bundle.active as active
    from core.compute import base_env, seeding
    monkeypatch.setattr(active, "get_bundle", lambda: type(
        "B", (), {"env_packs": [EnvPack("geo-base", {
            "name": "geo-base", "languages": ["python"], "role": "base",
            "spec": {"deps": {"conda": ["python =3.12", "ipykernel"]}}},
            "system")]})())
    base_env.reset_cache()
    monkeypatch.setattr(seeding, "adopt_env_id", lambda name: "env:v1:adopted")
    assert base_env.env_id("python") == "env:v1:adopted"
    base_env.reset_cache()
    monkeypatch.setattr(seeding, "adopt_env_id", lambda name: None)

    class _Stub:
        async def env_ensure(self, spec, **kw):
            return {"env_id": "env:v1:solved-privately", "status": "solved"}
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: _Stub())
    assert base_env.env_id("python") == "env:v1:solved-privately"
    base_env.reset_cache()
