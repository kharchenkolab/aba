"""Environment-aware execution routing (Stage 3): local=explicit-only; slurm=resource/walltime/speed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.router import decide

LOCAL = {"mode": "local", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0}
SLURM = {"mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
         "walltime_remaining_min": 240,
         "partitions": [{"partition": "normal", "cpus_per_node": 64, "mem_gb_per_node": 256, "gpu": False},
                        {"partition": "gpu", "cpus_per_node": 32, "mem_gb_per_node": 256, "gpu": True}]}

def loc(env, est=None, override=None):
    return decide(env=env, estimate=est, override=override).location

def test_local_never_auto_backgrounds():
    assert loc(LOCAL, {"runtime_min": 600}) == "local"                       # 10h estimate: still interactive
    assert loc(LOCAL, {"cores": 64, "mem_gb": 999, "gpu": True}) == "local"  # can't fit, but no slurm to escape to
    assert loc(LOCAL, override="background") == "background"                  # explicit honored

def test_slurm_fits_stays_local():
    assert loc(SLURM, {"runtime_min": 30, "cores": 4, "mem_gb": 8}) == "local"

def test_slurm_routes_on_gpu_mem_walltime_cores():
    assert loc(SLURM, {"gpu": True}) == "background"          # node 0 GPUs, a partition has one
    assert loc(SLURM, {"mem_gb": 64}) == "background"         # 64 > 0.85*32
    assert loc(SLURM, {"runtime_min": 200}) == "background"   # 200 > 0.8*240 walltime
    assert loc(SLURM, {"cores": 32}) == "background"          # 32 > 8 AND a partition offers 64

def test_slurm_more_cores_but_no_bigger_partition_stays_local():
    small = dict(SLURM, partitions=[{"partition": "p", "cpus_per_node": 4, "mem_gb_per_node": 16, "gpu": False}])
    assert loc(small, {"cores": 32}) == "local"              # Slurm nodes smaller → no point

def test_slurm_explicit_background():
    assert loc(SLURM, {}, override="background") == "background"
