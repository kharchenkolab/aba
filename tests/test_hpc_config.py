"""ondemand.md P6 — HPC resource config + estimate→request resolution."""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform

_PARTS = [
    {"name": "short", "max_cores": 4, "max_mem_gb": 16, "max_walltime_h": 2, "gpu": False},
    {"name": "long", "max_cores": 32, "max_mem_gb": 256, "max_walltime_h": 72, "gpu": False},
    {"name": "gpu", "max_cores": 16, "max_mem_gb": 128, "max_walltime_h": 24, "gpu": True},
]


def test_defaults_when_no_estimate():
    from core.jobs.hpc_config import resolve_resources
    r = resolve_resources({}, {"partitions": [], "defaults": {"cores": 2, "mem_gb": 8, "walltime_h": 6}})
    assert (r["cores"], r["mem_gb"], r["walltime_h"]) == (2, 8, 6)


def test_runtime_maps_to_walltime_ceil():
    from core.jobs.hpc_config import resolve_resources
    r = resolve_resources({"runtime_min": 130}, {"partitions": [], "defaults": {}})
    assert r["walltime_h"] == 3   # ceil(130/60)


def test_picks_first_fitting_partition_and_clamps():
    from core.jobs.hpc_config import resolve_resources
    # 8 cores + 5h → 'short' fails (cores>4, walltime>2) → 'long'
    r = resolve_resources({"cores": 8, "mem_gb": 32, "runtime_min": 300},
                          {"partitions": _PARTS, "defaults": {}})
    assert r["partition"] == "long" and r["cores"] == 8 and r["walltime_h"] == 5


def test_small_job_takes_short_partition():
    from core.jobs.hpc_config import resolve_resources
    r = resolve_resources({"cores": 2, "runtime_min": 30}, {"partitions": _PARTS, "defaults": {}})
    assert r["partition"] == "short"


def test_gpu_routes_to_gpu_partition():
    from core.jobs.hpc_config import resolve_resources
    r = resolve_resources({"gpu": True, "cores": 4}, {"partitions": _PARTS, "defaults": {}})
    assert r["partition"] == "gpu" and r["gpu"] is True


def test_clamp_to_partition_ceiling():
    from core.jobs.hpc_config import resolve_resources
    # asks 64 cores but the only fit clamps to its max
    cfg = {"partitions": [{"name": "big", "max_cores": 32, "max_mem_gb": 256,
                           "max_walltime_h": 72, "gpu": False}], "defaults": {}}
    r = resolve_resources({"cores": 64, "mem_gb": 512, "runtime_min": 60}, cfg)
    assert r["cores"] == 32 and r["mem_gb"] == 256
