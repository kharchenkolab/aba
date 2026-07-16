"""ComputeEnv: walltime parsing, the per-turn context line, and the live cluster
landscape read through the weft SitePort (Bucket 2 — the legacy slurm_live
introspection module was retired; partitions/load/access now come from the weft
site adapter)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.compute_env import slurm_time_to_min, _wait_label  # noqa: E402


def test_slurm_time_to_min():
    assert slurm_time_to_min("5-00:00:00") == 7200.0
    assert slurm_time_to_min("2:30:00") == 150.0
    assert slurm_time_to_min("45:00") == 45.0
    assert slurm_time_to_min("1-02:00:00") == 1560.0
    for bad in ("UNLIMITED", "INVALID", "", None, "NOT_SET"):
        assert slurm_time_to_min(bad) is None


def test_wait_label_from_live_load():
    """The weft-sourced wait signal: unavailable → idle → empty-queue → queued."""
    assert _wait_label(False, {}) == "unavailable"
    assert "quick" in _wait_label(True, {"cpus_idle": 8, "pending_jobs": 0})
    assert "moderate" in _wait_label(True, {"cpus_idle": 0, "pending_jobs": 0})
    assert "queued" in _wait_label(True, {"cpus_idle": 0, "pending_jobs": 3})


class _FakeAdapter:
    """Stands in for the weft SitePort — returns canned sites_describe /
    site_load / site_associations payloads (the real shapes)."""
    def __init__(self, describe, load, assoc):
        self._d, self._l, self._a = describe, load, assoc

    def sync_call(self, name, *a, **k):
        return {"sites_describe": self._d, "site_load": self._l,
                "site_associations": self._a}[name]


def test_cluster_landscape_maps_weft_payloads(monkeypatch):
    """_cluster_landscape maps weft's structured partitions + live load + assoc
    into the (partitions, user_access) shape context_line/describe_compute read."""
    import core.exec.compute_env as ce
    describe = {"capabilities": {"scheduler": {"partitions": [
        {"name": "normal", "cpus_per_node": 32, "mem_gb_per_node": 128,
         "max_walltime": "5-00:00:00", "available": True, "gres": []},
        {"name": "gpu", "cpus_per_node": 64, "mem_gb_per_node": 256,
         "max_walltime": "1-00:00:00", "available": True,
         "gres": [{"type": "gpu", "model": "a100", "count": 4}]}]}}}
    load = {"partitions": {
        "normal": {"cpus_idle": 32, "pending_jobs": 0},
        "gpu": {"cpus_idle": 0, "pending_jobs": 5}}}
    assoc = {"associations": [
        {"account": "lab", "partition": None, "allowed_qos": ["normal", "long"],
         "default_qos": "normal"}]}
    import core.compute as cc
    monkeypatch.setattr(cc, "get_compute",
                        lambda: _FakeAdapter(describe, load, assoc))
    parts, access = ce._cluster_landscape("cluster")
    pmap = {p["partition"]: p for p in parts}
    assert pmap["normal"]["gpu"] is False and pmap["gpu"]["gpu"] is True
    assert pmap["normal"]["cpus_per_node"] == 32
    assert "quick" in pmap["normal"]["wait"]        # idle CPUs → likely quick
    assert "queued" in pmap["gpu"]["wait"]          # no idle + pending → queued
    assert access == [{"account": "lab", "partition": None,
                       "qos": ["normal", "long"]}]


def test_context_line(monkeypatch):
    import core.exec.compute_env as ce
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {
        "mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
        "partitions": [{"partition": "gpu", "cpus_per_node": 32, "gpu": True, "wait": "likely quick"}]})
    line = ce.context_line()
    assert "slurm" in line and "8 cores / 32 GB" in line and "GPU" in line and "FRESH process" in line
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {
        "mode": "local", "node_cores": 4, "node_mem_gb": 16, "node_gpus": 0})
    l2 = ce.context_line()
    assert "local" in l2 and "background=True only" in l2 and "partitions" not in l2


def test_context_line_gpu_usable(monkeypatch):
    """The per-turn cue tells the agent whether a GPU step will actually accelerate."""
    import core.exec.compute_env as ce
    base = {"mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
            "partitions": [{"partition": "gpu", "cpus_per_node": 32, "gpu": True, "wait": "idle"}]}
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {**base, "gpu_usable": True})
    assert "GPU usable" in ce.context_line()
    monkeypatch.setattr(ce, "compute_env", lambda *a, **k: {
        **base, "gpu_usable": False, "gpu_usable_reason": "base torch is CPU-only — a GPU step would fall back to CPU"})
    warn = ce.context_line()
    assert "NOT usable" in warn and "CPU-only" in warn
