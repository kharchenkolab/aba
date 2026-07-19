"""QOS/account are resolved LIVE from the slurm-kind weft site's associations
(site_associations) at runtime — symmetric with live partitions, so no hpc.yaml
is needed to carry them; a configured catalog still wins, and the file is
unwrapped from its top-level `hpc:` key (the bug that made the installer-written
catalog silently ignored). Walltime is capped to the primary QOS's MaxWall.

Bucket 2: this replaces the retired slurm_live.qos_account_live (live sacctmgr
parsing) with the weft SitePort.
"""
import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_qos_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.jobs import hpc_config as HC          # noqa: E402

# The associations payload the weft slurm adapter returns (adapters/slurm.py):
# allowed_qos per association + a qos list carrying each QOS's raw MaxWall string.
_ASSOC = {
    "associations": [
        {"account": "labacct", "partition": "c",
         "allowed_qos": ["c_short", "c_medium", "long"], "default_qos": "c_short"},
        {"account": "labacct", "partition": "g",
         "allowed_qos": ["g_short", "long"], "default_qos": "g_short"}],
    "qos": [
        {"name": "long", "max_wall": "14-00:00:00"},
        {"name": "c_medium", "max_wall": "2-00:00:00"},
        {"name": "c_short", "max_wall": "08:00:00"},
        {"name": "g_short", "max_wall": "08:00:00"}],
}


class _FakeAdapter:
    def __init__(self, assoc):
        self._a = assoc

    def sync_call(self, name, *a, **k):
        assert name == "site_associations"
        return self._a


def _patch_live_assoc(monkeypatch, assoc=_ASSOC, site="cluster"):
    import core.jobs.weft_submitter as ws
    import core.compute as cc
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: site)
    monkeypatch.setattr(cc, "get_compute", lambda: _FakeAdapter(assoc))


def test_live_qos_account_ranks_by_maxwall(monkeypatch):
    _patch_live_assoc(monkeypatch)
    ranked, walls, account = HC._live_qos_account()
    assert account == "labacct"
    assert ranked[0] == "long"                       # 14d = most permissive → first
    assert set(ranked) == {"long", "c_medium", "c_short", "g_short"}
    assert walls["long"] == 14 * 24 and walls["c_short"] == 8


def test_hpc_config_fills_qos_account_live_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ABA_HPC_CONFIG", raising=False)
    monkeypatch.setattr("core.bundle.active.get_bundle",
                        lambda: (_ for _ in ()).throw(RuntimeError("no bundle")))
    monkeypatch.setattr(HC, "_live_partitions",
                        lambda: [{"name": "c", "max_cores": 22, "max_mem_gb": 76,
                                  "max_walltime_h": 1 << 30, "gpu": False}])
    _patch_live_assoc(monkeypatch)
    cfg = HC.hpc_config()
    assert cfg["qos"][0] == "long"
    assert cfg["account"] == "labacct"
    assert cfg["qos_max_walltime_h"] == 14 * 24
    r = HC.resolve_resources({"runtime_min": 100 * 60}, cfg)   # request 100h
    assert r["qos"] == "long" and r["account"] == "labacct"


def test_resolve_clamps_walltime_to_qos_maxwall():
    cfg = {"partitions": [{"name": "c", "max_cores": 22, "max_mem_gb": 76,
                           "max_walltime_h": 1 << 30, "gpu": False}],
           "qos": ["medium"], "qos_max_walltime_h": 48,
           "defaults": {"partition": "c", "cores": 1, "mem_gb": 4, "walltime_h": 4}}
    r = HC.resolve_resources({"runtime_min": 100 * 60}, cfg)        # 100h requested
    assert r["walltime_h"] == 48 and r["qos"] == "medium"          # clamped to QOS MaxWall


def test_configured_catalog_wins_and_is_unwrapped(monkeypatch, tmp_path):
    import yaml
    p = tmp_path / "hpc.yaml"
    # written WRAPPED under `hpc:` (installer/doc format) — must be unwrapped + honored
    p.write_text(yaml.safe_dump({"hpc": {
        "partitions": [{"name": "x", "max_cores": 4, "max_mem_gb": 8,
                        "max_walltime_h": 4, "gpu": False}],
        "qos": ["pinned"], "account": "myacct"}}))
    monkeypatch.setenv("ABA_HPC_CONFIG", str(p))
    # live associations must NOT be consulted when qos is configured
    import core.compute as cc
    monkeypatch.setattr(cc, "get_compute",
                        lambda: (_ for _ in ()).throw(AssertionError("live consulted")))
    cfg = HC.hpc_config()
    assert cfg["qos"] == ["pinned"] and cfg["account"] == "myacct"
    assert [q["name"] for q in cfg["partitions"]] == ["x"]     # file partitions, not live
