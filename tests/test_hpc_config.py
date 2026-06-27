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


def test_live_fallback_adapts_sinfo(monkeypatch):
    monkeypatch.setattr("core.jobs.slurm_live.partitions_live",
                        lambda: [{"partition": "big", "cpus_per_node": 64, "mem_gb_per_node": 256.0,
                                  "max_walltime": "5-00:00:00", "gpu": False}])
    monkeypatch.setattr("core.jobs.slurm_live.default_partition", lambda: "big")
    parts = _live_partitions()
    assert parts[0]["name"] == "big" and parts[0]["max_cores"] == 64 and parts[0]["max_walltime_h"] == 120
