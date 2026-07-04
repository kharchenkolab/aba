"""ComputeEnv + Slurm live-query parsers (parsers validated against real dev-cluster sinfo)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.compute_env import slurm_time_to_min
from core.jobs import slurm_live as sl


def test_slurm_time_to_min():
    assert slurm_time_to_min("5-00:00:00") == 7200.0
    assert slurm_time_to_min("2:30:00") == 150.0
    assert slurm_time_to_min("45:00") == 45.0
    assert slurm_time_to_min("1-02:00:00") == 1560.0
    for bad in ("UNLIMITED", "INVALID", "", None, "NOT_SET"):
        assert slurm_time_to_min(bad) is None


def test_parse_partitions_real_sinfo():
    out = "normal|up|5-00:00:00|1|1000|(null)|2|idle"          # the real dev-cluster line
    p0 = sl.parse_partitions(out)[0]
    assert p0["partition"] == "normal" and p0["avail"] == "up"
    assert p0["cpus_per_node"] == 1 and p0["mem_gb_per_node"] == 1.0
    assert p0["gpu"] is False and p0["nodes_total"] == 2 and p0["nodes_idle"] == 2


def test_parse_partitions_aggregates_and_gpu():
    out = "gpu|up|1-00:00:00|32|256000|gpu:4|3|idle\ngpu|up|1-00:00:00|32|256000|gpu:4|1|alloc"
    p = {x["partition"]: x for x in sl.parse_partitions(out)}["gpu"]
    assert p["gpu"] is True and p["cpus_per_node"] == 32
    assert p["nodes_total"] == 4 and p["nodes_idle"] == 3     # alloc not counted idle


def test_parse_queue_and_wait():
    q = sl.parse_queue("normal|R\nnormal|PD\nnormal|PD\ngpu|R")
    assert q["normal"] == {"pending": 2, "running": 1}
    assert "quick" in sl.wait_label({"partition": "normal", "avail": "up", "nodes_idle": 2}, q)
    assert "queued" in sl.wait_label({"partition": "normal", "avail": "up", "nodes_idle": 0}, q)


def test_parse_assoc():
    assert sl.parse_assoc("") == []
    rows = sl.parse_assoc("kharchenko|normal|normal,high\n")
    assert rows[0]["account"] == "kharchenko" and "high" in rows[0]["qos"]


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
