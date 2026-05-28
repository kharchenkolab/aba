"""
K1: persistent kernel abstraction — state persistence, isolation, interrupt,
idle reap, and the per-user cap with LRU. Real jupyter kernels (subprocess), no
model. First run registers the aba_py kernelspec (slightly slower).

Run:
    .venv/bin/python tests/k1_kernels.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_k1_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "k1.db")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.exec.kernels.jupyter import JupyterKernelSession   # noqa: E402
from core.exec.kernels.pool import KernelPool                # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        _failures.append(label)


class FakeCancelToken:
    def __init__(self):
        self.run_id = "k1"; self.cancelled = False; self.reason = ""; self._cbs = []

    def register(self, cb):
        self._cbs.append(cb)
        return lambda: self._cbs.remove(cb) if cb in self._cbs else None

    def cancel(self, reason="stop"):
        self.cancelled = True; self.reason = reason
        for cb in list(self._cbs):
            cb()


def main() -> int:
    cwd = tempfile.mkdtemp(prefix="aba_k1_cwd_")

    print("persistence + isolation")
    s = JupyterKernelSession("thread-A", "python", cwd=cwd)
    try:
        r = s.execute("x = 41 + 1\nprint('x is', x)")
        check("first cell runs", r.returncode == 0 and "x is 42" in r.stdout, str(r))
        r2 = s.execute("print('reuse', x)")          # state persists across calls
        check("state persists across execute calls", r2.returncode == 0 and "reuse 42" in r2.stdout, str(r2))
        rerr = s.execute("print(undefined_name)")
        check("error captured as traceback", rerr.returncode != 0 and "NameError" in rerr.stderr, str(rerr)[:200])

        print("interrupt (Stop) leaves the session alive")
        tok = FakeCancelToken()
        threading.Timer(0.6, lambda: tok.cancel("stop")).start()
        t0 = time.time()
        rc = s.execute("import time\nfor _ in range(300): time.sleep(0.1)", cancel_token=tok, timeout_s=60)
        elapsed = time.time() - t0
        check("interrupt returns promptly", elapsed < 10, f"{elapsed:.1f}s")
        check("flagged cancelled", rc.cancelled, str(rc)[:160])
        ralive = s.execute("print('still', x)")      # state intact after interrupt
        check("session alive + state intact after interrupt", ralive.returncode == 0 and "still 42" in ralive.stdout)
    finally:
        s.shutdown()
        check("shutdown marks not-alive", not s.alive)

    print("isolation: a separate scope has no shared state")
    s2 = JupyterKernelSession("thread-B", "python", cwd=tempfile.mkdtemp())
    try:
        r = s2.execute("print(x)")
        check("distinct scope → NameError (isolated)", r.returncode != 0 and "NameError" in r.stderr)
    finally:
        s2.shutdown()

    print("pool: idle reap + cap/LRU")
    pool = KernelPool(max_live=2, idle_ttl=900)
    try:
        a = pool.get_or_start("t1", "python", cwd=tempfile.mkdtemp())
        a.execute("v=1")
        check("pool starts a session", pool.live_count() == 1)
        pool.get_or_start("t2", "python", cwd=tempfile.mkdtemp())
        pool.get_or_start("t3", "python", cwd=tempfile.mkdtemp())   # exceeds cap=2 → LRU evict
        check("cap enforced (LRU evicts)", pool.live_count() == 2, f"live={pool.live_count()}")
        n = pool.reap_idle(ttl=0)                                    # everything is 'idle' at ttl=0
        check("idle reap culls sessions", n >= 1 and pool.live_count() == 0, f"reaped={n} live={pool.live_count()}")
    finally:
        pool.shutdown_all()

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL K1 KERNEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
