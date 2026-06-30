"""Kernel liveness accessor + dead-kernel watchdog (orphaned-uvicorn incident).

Regression: under jupyter_client 8.x the Popen lives at provisioner.process and
the pid at provisioner.pid; the old accessor checked km.kernel / provisioner.proc
only, so kernel_pid() returned None — owned_kernel_pids() found nothing and the
shutdown handler never reaped kernels (orphan/zombie pileup)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.kernels.jupyter import JupyterKernelSession


class _FakeProc:
    def __init__(self, pid, alive): self.pid = pid; self._alive = alive
    def poll(self): return None if self._alive else 0


def _session(km):
    s = JupyterKernelSession.__new__(JupyterKernelSession)   # skip __init__ (no kernel)
    s._km = km
    return s


def _km(process=None, proc=None, pid=None):
    return type("KM", (), {"provisioner": type("P", (), {"process": process, "proc": proc, "pid": pid})()})()


def test_kernel_pid_resolves_jupyter_client_8x_provisioner():
    p = _FakeProc(4242, alive=True)
    s = _session(_km(process=p, pid=4242))
    assert s.kernel_pid() == 4242            # used to return None
    assert s.kernel_dead() is False
    p._alive = False
    assert s.kernel_dead() is True           # watchdog can now detect death


def test_kernel_pid_falls_back_to_provisioner_pid():
    assert _session(_km(pid=7777)).kernel_pid() == 7777


def test_kernel_dead_unknown_is_not_dead():
    # No proc + no pid → must NOT falsely report dead (would kill live turns).
    assert _session(_km()).kernel_dead() is False


# --- P1: bounded kernel startup (start_kernel / start_channels can't hang forever) ---
import time
import pytest


class _ShutdownSpyKM:
    def __init__(self): self.shutdown_called = False
    def shutdown_kernel(self, now=False): self.shutdown_called = True


def test_start_bounded_returns_fast_result():
    s = _session(_ShutdownSpyKM())
    assert s._start_bounded(lambda: 42, "start_kernel", 5.0) == 42
    assert s._km.shutdown_called is False     # success path: no kill


def test_start_bounded_times_out_and_reaps():
    s = _session(_ShutdownSpyKM())
    t0 = time.time()
    with pytest.raises(TimeoutError):
        s._start_bounded(lambda: time.sleep(10), "start_kernel", 0.4)
    assert time.time() - t0 < 2.0             # fired near the deadline, not at 10s
    assert s._km.shutdown_called is True      # half-started kernel best-effort reaped


def test_start_bounded_surfaces_worker_error():
    s = _session(_ShutdownSpyKM())
    def _boom(): raise RuntimeError("zmq EAGAIN")
    with pytest.raises(RuntimeError, match="zmq EAGAIN"):
        s._start_bounded(_boom, "start_channels", 5.0)
