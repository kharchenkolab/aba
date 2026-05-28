"""KernelPool — owns live kernel sessions, keyed by (scope_key, lang).

Conservative lifecycle (kernels.md §4): lazy start, per-user cap with LRU
eviction, and a background reaper that culls sessions idle past the TTL. A
single process-wide pool; the reaper starts on first use.
"""
from __future__ import annotations
import threading
import time
from typing import Optional

from core.config import KERNEL_IDLE_TTL_S, KERNEL_MAX_LIVE


class KernelPool:
    def __init__(self, max_live: int = KERNEL_MAX_LIVE, idle_ttl: int = KERNEL_IDLE_TTL_S):
        self._lock = threading.RLock()
        self._sessions: dict[tuple[str, str], object] = {}
        self._max = max_live
        self._ttl = idle_ttl
        self._reaper_started = False

    def get_or_start(self, scope_key: str, lang: str, *, cwd: str):
        from core.exec.kernels.jupyter import JupyterKernelSession
        with self._lock:
            self._start_reaper()
            key = (scope_key, lang)
            s = self._sessions.get(key)
            if s is not None and getattr(s, "alive", False):
                s.touch()
                return s
            if s is not None:
                self._sessions.pop(key, None)        # dead handle — drop
            # Per-user cap: evict least-recently-used until there's room.
            while len(self._sessions) >= self._max:
                lru = min(self._sessions, key=lambda k: self._sessions[k].last_used)
                self._sessions.pop(lru).shutdown()
            s = JupyterKernelSession(scope_key, lang, cwd=cwd)
            self._sessions[key] = s
            return s

    def restart(self, scope_key: str, lang: str = "python") -> bool:
        """Clear a thread's session (hard reset). Next use starts fresh."""
        with self._lock:
            s = self._sessions.pop((scope_key, lang), None)
        if s is not None:
            s.shutdown()
            return True
        return False

    def reap_idle(self, ttl: Optional[int] = None) -> int:
        ttl = self._ttl if ttl is None else ttl
        now = time.time()
        removed = 0
        with self._lock:
            for key in list(self._sessions):
                s = self._sessions[key]
                if not getattr(s, "alive", False) or (now - s.last_used) > ttl:
                    self._sessions.pop(key).shutdown()
                    removed += 1
        return removed

    def shutdown_all(self) -> None:
        with self._lock:
            for key in list(self._sessions):
                try:
                    self._sessions.pop(key).shutdown()
                except Exception:  # noqa: BLE001
                    pass

    def live_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _start_reaper(self) -> None:
        if self._reaper_started:
            return
        self._reaper_started = True

        def loop():
            while True:
                time.sleep(60)
                try:
                    self.reap_idle()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=loop, daemon=True, name="kernel-reaper").start()


_POOL: Optional[KernelPool] = None


def get_pool() -> KernelPool:
    global _POOL
    if _POOL is None:
        _POOL = KernelPool()
        # Reap kernels on process exit so a backend/test shutdown doesn't leave
        # orphaned kernels (which log "Parent appears to have exited").
        import atexit
        atexit.register(_POOL.shutdown_all)
    return _POOL
