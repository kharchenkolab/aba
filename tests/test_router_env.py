"""Environment-aware execution routing (Stage 3): local=explicit-only; slurm=resource/walltime/speed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.router import decide

LOCAL = {"mode": "local", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0}
SLURM = {"mode": "slurm", "node_cores": 8, "node_mem_gb": 32, "node_gpus": 0,
         "walltime_remaining_min": 240,
         "partitions": [{"partition": "normal", "cpus_per_node": 64, "mem_gb_per_node": 256, "gpu": False},
                        {"partition": "gpu", "cpus_per_node": 32, "mem_gb_per_node": 256, "gpu": True}]}

def loc(env, est=None, override=None):
    return decide(env=env, estimate=est, override=override).location

def rat(env, est=None, override=None):
    return decide(env=env, estimate=est, override=override).rationale

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


# ── the un-characterised call: every clause above is gated on a supplied
# estimate, so a bare run_python(code=…) is checked against NOTHING. It still
# runs locally — that part is deliberate — but the rationale must not claim a
# fit that was never tested. The placement knowhow used to advertise the router
# as auto-routing the cases that "won't fit / would be killed"; believing that
# is exactly what makes a bare call feel safe on a 3-hour job in a 45-minute
# session, and nothing downstream objects.

def _unchecked(r):
    """The rationale must say the check did not happen, and must NOT assert a fit."""
    assert "no estimate" in r.lower(), f"does not say the estimate was missing: {r!r}"
    assert "fit" not in r.replace("fit or walltime check was possible", ""), (
        f"claims a fit that was never checked: {r!r}")

def test_no_estimate_is_not_reported_as_a_fit():
    assert loc(SLURM, {}) == "local"          # placement unchanged — only honesty
    _unchecked(rat(SLURM, {}))

@pytest.mark.parametrize("est", [
    None,                                              # arg omitted entirely
    {},                                                # empty dict
    {"cores": None, "mem_gb": None, "gpu": None, "runtime_min": None},
    {"cores": 0, "mem_gb": 0, "gpu": False, "runtime_min": 0},
])
def test_every_shape_of_absent_estimate_takes_the_unchecked_branch(est):
    """DEGENERATE: the tools emit absence as None, as 0, and as a missing key.
    All three mean 'nobody characterised this' and must read the same."""
    assert loc(SLURM, est) == "local"
    _unchecked(rat(SLURM, est))

def test_a_real_estimate_that_fits_still_says_so():
    """THE OTHER SIDE: a guard that only checked the bare call would pass if the
    router simply stopped ever claiming a fit. When the numbers ARE supplied and
    genuinely fit, that is a checked conclusion and must still be stated."""
    r = rat(SLURM, {"runtime_min": 30, "cores": 4, "mem_gb": 8})
    assert "fits this node" in r, f"a checked fit is no longer reported: {r!r}"
    assert "no estimate" not in r.lower()

def test_the_two_rationales_differ():
    """WIDE: a constant string would satisfy one of the above by accident."""
    assert rat(SLURM, {}) != rat(SLURM, {"runtime_min": 30, "cores": 4, "mem_gb": 8})
