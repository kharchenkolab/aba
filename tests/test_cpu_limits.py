"""Allocation-aware CPU sizing for BLAS/OMP thread pools (core/exec/cpu.py).

Regression for the OnDemand finding: a Slurm node allocated 1 CPU out of 56 must
size kernel BLAS threads to 1, or OpenBLAS spawns one thread per host core (56),
hits the per-user process limit, and dies on pthread EAGAIN — taking run_r /
run_python / IRkernel::installspec down with it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.exec import cpu as cpu_mod  # noqa: E402

pytestmark = pytest.mark.platform


@pytest.fixture
def env(monkeypatch):
    """Deterministic baseline: no allocation env vars, 56 visible CPUs, no cgroup
    quota. Restores the thread env vars on teardown (pin_blas_threads mutates
    os.environ directly, which monkeypatch would not revert)."""
    saved = {v: os.environ.get(v) for v in cpu_mod._THREAD_ENV_VARS}
    for v in ("ABA_CPU_LIMIT", "SLURM_CPUS_PER_TASK", "SLURM_CPUS_ON_NODE",
              "ABA_KERNEL_THREADS", *cpu_mod._THREAD_ENV_VARS):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(cpu_mod, "_cgroup_cpu_quota", lambda: None)
    monkeypatch.setattr(cpu_mod.os, "sched_getaffinity", lambda pid: set(range(56)))
    yield monkeypatch
    for v, val in saved.items():
        if val is None:
            os.environ.pop(v, None)
        else:
            os.environ[v] = val


def test_slurm_one_cpu_of_many_caps_to_one(env):
    # the live OOD case: SLURM_CPUS_ON_NODE=1 but 56 CPUs visible
    env.setenv("SLURM_CPUS_ON_NODE", "1")
    assert cpu_mod.effective_cpu_count() == 1
    assert cpu_mod.default_thread_cap() == 1


def test_multicore_allocation_honored_in_full(env):
    # a "Heavy" production node allocated 16 cores must get 16 BLAS threads,
    # NOT a fixed cap — the operator asked for the cores.
    env.setenv("SLURM_CPUS_ON_NODE", "16")
    assert cpu_mod.effective_cpu_count() == 16
    assert cpu_mod.default_thread_cap() == 16


def test_large_allocation_honored_above_legacy_cap(env):
    env.setenv("SLURM_CPUS_ON_NODE", "32")
    assert cpu_mod.default_thread_cap() == 32  # explicit allocation, no 8-cap


def test_takes_min_of_all_constraints(env):
    env.setenv("SLURM_CPUS_ON_NODE", "10")
    env.setenv("SLURM_CPUS_PER_TASK", "4")
    assert cpu_mod.effective_cpu_count() == 4  # slurm 10 & 4 -> 4
    assert cpu_mod.default_thread_cap() == 4


def test_allocation_never_exceeds_affinity(env):
    # SLURM claims 32 but the cpuset only permits 8 -> never run 32 threads
    env.setenv("SLURM_CPUS_ON_NODE", "32")
    env.setattr(cpu_mod.os, "sched_getaffinity", lambda pid: set(range(8)))
    assert cpu_mod.effective_cpu_count() == 8
    assert cpu_mod.default_thread_cap() == 8


def test_explicit_limit_wins_when_lowest(env):
    env.setenv("SLURM_CPUS_ON_NODE", "8")
    env.setenv("ABA_CPU_LIMIT", "2")
    assert cpu_mod.effective_cpu_count() == 2
    assert cpu_mod.default_thread_cap() == 2


def test_unscheduled_fat_box_caps_at_8(env):
    # NO allocation signal, 56 CPUs visible -> conservative cap so small bio
    # matrices don't oversubscribe.
    assert cpu_mod.effective_cpu_count() == 56   # usable cpus
    assert cpu_mod.default_thread_cap() == 8     # but threads capped


def test_unscheduled_small_box_uses_all(env):
    env.setattr(cpu_mod.os, "sched_getaffinity", lambda pid: {0, 1, 2, 3})
    assert cpu_mod.effective_cpu_count() == 4
    assert cpu_mod.default_thread_cap() == 4


def test_cgroup_quota_is_an_allocation_signal(env):
    env.setattr(cpu_mod, "_cgroup_cpu_quota", lambda: 3)
    assert cpu_mod.effective_cpu_count() == 3   # min(56 affinity, 3 quota)
    assert cpu_mod.default_thread_cap() == 3    # honored as an allocation


def test_fallback_to_cpu_count(env):
    def _no_affinity(pid):
        raise AttributeError
    env.setattr(cpu_mod.os, "sched_getaffinity", _no_affinity)
    env.setattr(cpu_mod.os, "cpu_count", lambda: 12)
    assert cpu_mod.effective_cpu_count() == 12


def test_kernel_threads_operator_override_wins(env):
    env.setenv("SLURM_CPUS_ON_NODE", "1")
    env.setenv("ABA_KERNEL_THREADS", "16")
    assert cpu_mod.default_thread_cap() == 16  # explicit intent beats everything


def test_pin_sets_unset_vars(env):
    env.setenv("SLURM_CPUS_ON_NODE", "1")
    n = cpu_mod.pin_blas_threads()
    assert n == 1
    for v in cpu_mod._THREAD_ENV_VARS:
        assert os.environ[v] == "1", v


def test_pin_respects_preset_value(env):
    env.setenv("SLURM_CPUS_ON_NODE", "1")
    os.environ["OPENBLAS_NUM_THREADS"] = "4"  # operator/launch-script pre-set
    cpu_mod.pin_blas_threads()
    assert os.environ["OPENBLAS_NUM_THREADS"] == "4"  # setdefault respects it
