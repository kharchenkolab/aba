"""KernelPool busy-aware eviction — never kill a kernel mid-execution (the
cross-thread stall: a long-running analysis kernel was evicted as 'LRU' when
newer threads pushed the pool over the cap)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import pytest
import core.exec.kernels.jupyter as jmod
from core.exec.kernels.pool import KernelPool, KernelCapacityError


class _FakeSession:
    _ctr = 0
    _base = time.time()
    def __init__(self, scope_key, lang, *, cwd, env_name=None):
        _FakeSession._ctr += 1
        self.scope_key, self.lang = scope_key, lang
        self.alive, self.busy = True, False
        self.last_used = _FakeSession._base + _FakeSession._ctr * 0.01   # recent + ordered
        self.shutdown_called = False
    def touch(self): self.last_used = _FakeSession._base + 1e6
    def shutdown(self): self.shutdown_called = True; self.alive = False
    def kernel_pid(self): return None


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setattr(jmod, "JupyterKernelSession", _FakeSession)

def _pool(soft, hard):
    return KernelPool(max_live=soft, idle_ttl=10**9, hard_max=hard)


def test_soft_cap_evicts_idle_lru():
    p = _pool(2, 5)
    a = p.get_or_start("A", "r", cwd="/t")
    p.get_or_start("B", "r", cwd="/t")
    p.get_or_start("C", "r", cwd="/t")              # over soft cap → evict idle LRU (A)
    assert p.live_count() == 2 and a.shutdown_called

def test_busy_kernel_never_evicted():
    p = _pool(2, 5)
    a = p.get_or_start("A", "r", cwd="/t"); a.busy = True   # A is executing
    b = p.get_or_start("B", "r", cwd="/t")                  # idle, older-than-C
    p.get_or_start("C", "r", cwd="/t")             # over cap → must evict B, NOT busy A
    assert a.alive and not a.shutdown_called and p.peek("A", "r") is a
    assert b.shutdown_called

def test_hard_cap_refuses_rather_than_kill_busy():
    p = _pool(2, 3)
    for name in ("A", "B", "C"):
        p.get_or_start(name, "r", cwd="/t").busy = True     # 3 busy (= hard cap)
    assert p.live_count() == 3
    with pytest.raises(KernelCapacityError):
        p.get_or_start("D", "r", cwd="/t")         # all busy + at hard cap → refuse, don't kill
    assert p.live_count() == 3
