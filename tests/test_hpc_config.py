"""hpc_config: estimate→partition routing + the live-sinfo fallback (skipping hpc.yaml)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.jobs.hpc_config import resolve_resources, _live_partitions  # noqa: E402

CAT = {"partitions": [
    {"name": "normal", "max_cores": 16, "max_mem_gb": 64, "max_walltime_h": 24, "gpu": False},
    {"name": "gpu",    "max_cores": 32, "max_mem_gb": 256, "max_walltime_h": 24, "gpu": True}],
    "defaults": {"partition": "normal"}}


def test_general_job_uses_default_partition():
    assert resolve_resources({"cores": 4, "mem_gb": 16}, CAT)["partition"] == "normal"


def test_gpu_job_routes_to_gpu_partition():
    assert resolve_resources({"gpu": True}, CAT)["partition"] == "gpu"


def test_oversized_job_clamps_to_largest_fitting():
    r = resolve_resources({"cores": 64}, CAT)
    assert r["partition"] == "normal" and r["cores"] == 16


def test_unconfigured_omits_partition_qos_account():
    # no config + no sinfo → empty catalog → submitter omits --partition/--qos/--account
    r = resolve_resources({"cores": 2}, {"partitions": [], "defaults": {}})
    assert r["partition"] is None and r["qos"] is None and r["account"] is None


class _FakeAdapter:
    """Weft SitePort stand-in returning a canned sites_describe payload."""
    def __init__(self, describe):
        self._d = describe

    def sync_call(self, name, *a, **k):
        assert name == "sites_describe"
        return self._d


def test_live_fallback_adapts_weft_partitions(monkeypatch):
    """_live_partitions adapts the weft site's structured partition capabilities
    (sites_describe) into the catalog shape — GPUs from gres, walltime hours from
    the unambiguous max_walltime_s."""
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: "cluster")
    import core.compute as cc
    describe = {"capabilities": {"scheduler": {"partitions": [
        {"name": "big", "cpus_per_node": 64, "mem_gb_per_node": 256,
         "max_walltime_s": 5 * 24 * 3600, "gres": []},
        {"name": "gpu", "cpus_per_node": 32, "mem_gb_per_node": 128,
         "max_walltime_s": 24 * 3600,
         "gres": [{"type": "gpu", "count": 4}]}]}}}
    monkeypatch.setattr(cc, "get_compute", lambda: _FakeAdapter(describe))
    parts = {p["name"]: p for p in _live_partitions()}
    assert parts["big"]["max_cores"] == 64 and parts["big"]["max_walltime_h"] == 120
    assert parts["big"]["gpu"] is False and parts["gpu"]["gpu"] is True


def test_live_partitions_empty_without_cluster_site(monkeypatch):
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws, "weft_slurm_site", lambda: None)
    assert _live_partitions() == []
