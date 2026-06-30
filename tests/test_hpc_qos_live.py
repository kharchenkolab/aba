"""QOS/account are resolved LIVE from sacctmgr at runtime (symmetric with live
partitions), so no hpc.yaml is needed to carry them; a configured catalog still
wins, and the file is unwrapped from its top-level `hpc:` key (the bug that made
the installer-written catalog silently ignored). Walltime is capped to the
primary QOS's MaxWall.
"""
import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_qos_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.jobs import slurm_live as SL          # noqa: E402
from core.jobs import hpc_config as HC          # noqa: E402

_ASSOC = "labacct|c|c_short,c_medium,long\nlabacct|g|g_short,long\n"   # Account|Partition|QOS
_QOS = "long|14-00:00:00\nc_medium|2-00:00:00\nc_short|08:00:00\ng_short|08:00:00\n"


def _fake_run(cmd, timeout=8):
    s = " ".join(cmd)
    if "show assoc" in s:
        return _ASSOC
    if "show qos" in s:
        return _QOS
    return None


def test_qos_account_live_ranks_by_maxwall(monkeypatch):
    monkeypatch.setattr(SL, "_run", _fake_run)
    SL.qos_account_live.cache_clear()
    try:
        ranked, walls, account = SL.qos_account_live()
        assert account == "labacct"
        assert ranked[0] == "long"                       # 14d = most permissive → first
        assert set(ranked) == {"long", "c_medium", "c_short", "g_short"}
        assert walls["long"] == 14 * 24 and walls["c_short"] == 8
    finally:
        SL.qos_account_live.cache_clear()


def test_hpc_config_fills_qos_account_live_when_unconfigured(monkeypatch):
    monkeypatch.delenv("ABA_HPC_CONFIG", raising=False)
    monkeypatch.setattr("core.bundle.active.get_bundle",
                        lambda: (_ for _ in ()).throw(RuntimeError("no bundle")))
    monkeypatch.setattr(HC, "_live_partitions",
                        lambda: [{"name": "c", "max_cores": 22, "max_mem_gb": 76,
                                  "max_walltime_h": 1 << 30, "gpu": False}])
    monkeypatch.setattr(SL, "_run", _fake_run)
    SL.qos_account_live.cache_clear()
    try:
        cfg = HC.hpc_config()
        assert cfg["qos"][0] == "long"
        assert cfg["account"] == "labacct"
        assert cfg["qos_max_walltime_h"] == 14 * 24
        r = HC.resolve_resources({"runtime_min": 100 * 60}, cfg)   # request 100h
        assert r["qos"] == "long" and r["account"] == "labacct"
    finally:
        SL.qos_account_live.cache_clear()


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
    # live must NOT be consulted when qos is configured
    monkeypatch.setattr(SL, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live consulted")))
    SL.qos_account_live.cache_clear()
    try:
        cfg = HC.hpc_config()
        assert cfg["qos"] == ["pinned"] and cfg["account"] == "myacct"
        assert [p["name"] for p in cfg["partitions"]] == ["x"]     # file partitions, not live
    finally:
        SL.qos_account_live.cache_clear()
