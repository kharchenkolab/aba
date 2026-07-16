"""weft-sites.yaml writer (misc/compute_settings.md §3b/§7): merge-by-name,
aba-keys preservation, atomicity, and the roundtrip with the boot reader."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_sites_write_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core.compute import sites_config as sc  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "home"))
    yield


def test_upsert_creates_file_and_defaults_aba_block():
    entry = sc.upsert_site("vbc", "slurm", {"host": "login.vbc.ac.at",
                                            "root": "/scratch/me/.weft"})
    assert entry["aba"]["use_for"] == ["interactive", "background"]
    doc = sc.read_sites_config()
    assert doc["sites"][0]["name"] == "vbc"
    assert doc["sites"][0]["config"]["host"] == "login.vbc.ac.at"


def test_upsert_merges_aba_and_preserves_unknown_keys():
    from core.compute.adapter import sites_config_path
    sites_config_path().parent.mkdir(parents=True, exist_ok=True)
    sites_config_path().write_text(
        "# operator comment is lost, structure is not\n"
        "defaults: {solver: mamba}\n"
        "sites:\n"
        "  - name: vbc\n    kind: slurm\n"
        "    config: {host: old.host, root: /old}\n"
        "    operator_note: keep-me\n"
        "    aba: {contract: shared-fs, use_for: [background]}\n")
    sc.upsert_site("vbc", "slurm", {"host": "new.host", "root": "/new"},
                   aba={"use_for": ["interactive", "background", "gpu"]})
    doc = sc.read_sites_config()
    assert doc["defaults"] == {"solver": "mamba"}          # top-level survives
    entry = doc["sites"][0]
    assert entry["operator_note"] == "keep-me"             # per-site survives
    assert entry["config"] == {"host": "new.host", "root": "/new"}  # replaced
    assert entry["aba"]["contract"] == "shared-fs"         # merged, not replaced
    assert entry["aba"]["use_for"] == ["interactive", "background", "gpu"]


def test_aba_keys_lookup():
    sc.upsert_site("vbc", "slurm", {"root": "/x"},
                   aba={"contract": "shared-fs",
                        "storage": [{"path": "/groups/lab", "stable": True}]})
    assert sc.aba_keys("vbc")["storage"] == [{"path": "/groups/lab",
                                              "stable": True}]
    assert sc.aba_keys("nope") == {}


def test_remove_site():
    sc.upsert_site("a", "local", {"root": "/a"})
    sc.upsert_site("b", "local", {"root": "/b"})
    assert sc.remove_site("a") is True
    assert sc.remove_site("a") is False
    assert [e["name"] for e in sc.list_declared_sites()] == ["b"]


def test_write_is_atomic():
    import unittest.mock as mock
    sc.upsert_site("vbc", "slurm", {"root": "/x"})
    before = sc.read_sites_config()

    with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            sc.upsert_site("other", "local", {"root": "/y"})
    assert sc.read_sites_config() == before     # original intact, not truncated


def test_roundtrip_with_boot_reader():
    """What the tab writes is exactly what _register_configured_sites reads:
    same file, same {name, kind, config} triple (aba: keys ignored by weft)."""
    sc.upsert_site("scratch2", "local", {"root": f"{_tmp}/site2-root"},
                   aba={"contract": "shared-fs", "use_for": ["background"]})
    from core.compute import adapter as ad
    if ad.resolve_pixi() is None:
        pytest.skip("weft/pixi unavailable")
    try:
        import weft.api  # noqa: F401
    except Exception:
        pytest.skip("weft package not installed")
    import unittest.mock as mock
    before_adapter, before_status = ad._adapter, dict(ad._status)
    try:
        with mock.patch.dict(os.environ,
                             {"ABA_WEFT_WORKSPACE": f"{_tmp}/weft-ws-rt"}):
            ad._adapter = None
            st = ad.configure()
            assert st["ok"], st["detail"]
            names = {s["name"] for s in ad.get_compute().sync_call("sites_list")}
    finally:
        # FULL restore — leaving _status "ok" with _adapter None poisons any
        # later test that calls get_compute (found via test_jobs_tools)
        ad.shutdown()
        ad._adapter, ad._status = before_adapter, before_status
    assert {"local", "scratch2"} <= names
