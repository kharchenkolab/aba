"""KernelPool — owns live kernel sessions, keyed by (scope_key, lang).

Conservative lifecycle (kernels.md §4): lazy start, per-user cap with LRU
eviction, and a background reaper that culls sessions idle past the TTL. A
single process-wide pool; the reaper starts on first use.
"""
from __future__ import annotations
import threading
import time
from typing import Optional

from core.config import KERNEL_IDLE_TTL_S, KERNEL_MAX_LIVE, KERNEL_HARD_MAX


class KernelCapacityError(RuntimeError):
    """Raised when a new kernel is needed but every live kernel is BUSY and the
    hard cap is reached — we refuse rather than evict a kernel mid-execution
    (which would destroy another thread's running analysis)."""


class KernelPool:
    def __init__(self, max_live: int = KERNEL_MAX_LIVE, idle_ttl: int = KERNEL_IDLE_TTL_S,
                 hard_max: int = KERNEL_HARD_MAX):
        self._lock = threading.RLock()
        self._sessions: dict[tuple[str, str], object] = {}
        self._max = max_live
        self._hard_max = max(hard_max, max_live)   # never below the soft cap
        self._ttl = idle_ttl
        self._reaper_started = False

    def _new_session(self, scope_key: str, lang: str, *, cwd: str, env_name: str | None,
                     site: str = "local"):
        """Build a fresh kernel session for (scope_key, lang) — on the weft
        transport, the ONLY kernel transport (the legacy local lane and its
        silent fallback were retired with the cutover: a substrate error is a
        LOUD, typed refusal, never a quiet lane switch — the run tool degrades
        to its one-shot lane and says so). An unknown named env raises with the
        clear cause instead of guessing."""
        from core.exec.kernels import weft as _weft
        s = _weft.for_pool(scope_key, lang, cwd=cwd, env_name=env_name, site=site)
        if s is None:
            raise RuntimeError(
                f"no kernel available for site {site!r} (unknown env "
                f"{env_name!r}? — inspect_env() lists this project's envs)")
        return s

    def get_or_start(self, scope_key: str, lang: str, *, cwd: str, env_name: str | None = None,
                     site: str = "local"):
        with self._lock:
            self._start_reaper()
            key = (scope_key, lang)
            s = self._sessions.get(key)
            if s is not None and getattr(s, "alive", False):
                s.touch()
                return s
            if s is not None:
                self._sessions.pop(key, None)        # dead handle — drop
            # Per-user soft cap: evict the least-recently-used IDLE session to make
            # room. NEVER evict a BUSY one — shutting a kernel down mid-execution
            # destroys the running analysis (the cross-thread stall). When every
            # over-cap session is busy, allow a bounded burst above the soft cap;
            # only refuse past the hard cap.
            while len(self._sessions) >= self._max:
                idle = [k for k, v in self._sessions.items() if not getattr(v, "busy", False)]
                if not idle:
                    break                                    # all busy — don't kill work
                lru = min(idle, key=lambda k: self._sessions[k].last_used)
                self._sessions.pop(lru).shutdown()
            if len(self._sessions) >= self._hard_max:
                raise KernelCapacityError(
                    f"Compute at capacity: {len(self._sessions)} kernels live and all busy "
                    f"(hard cap {self._hard_max}). Wait for a running analysis to finish, or "
                    f"stop one, before starting another.")
            s = self._new_session(scope_key, lang, cwd=cwd, env_name=env_name, site=site)
            self._sessions[key] = s
            return s

    def sessions_for_thread(self, thread_id: str) -> list:
        """Every live session whose scope belongs to `thread_id` — the bare
        scope, @site variants, and ::env variants. Addressing needs the full
        set: a durable (target, rel) handle for a site-kernel's output can
        only be recorded if the SITE-scoped session is discoverable (the
        default-scope peek silently missed `tid@site`, so no-plan threads
        never recorded their remote targets — live 2026-07-23)."""
        tid = str(thread_id)
        out = []
        with self._lock:
            for (sk, _lang), sess in list(self._sessions.items()):
                if sk == tid or sk.startswith(tid + "@")                         or sk.startswith(tid + "::"):
                    out.append(sess)
        return out

    def peek(self, scope_key: str, lang: str = "python"):
        """Return the live session for (scope_key, lang) if one exists, else None
        — WITHOUT starting a new kernel. Used to nudge an already-running kernel
        (e.g. invalidate import caches after an overlay install)."""
        with self._lock:
            s = self._sessions.get((scope_key, lang))
            return s if (s is not None and getattr(s, "alive", False)) else None

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

    def evict_env_sessions(self, env_name: str) -> int:
        """Shut down every live session attached to the named env (scope keys
        carry `::env::<name>`, local AND remote lanes). Called when the env's
        IDENTITY changes (extension mints a new frozen EnvID — a running kernel
        stays on the old realization and new packages never appear in it;
        found live by env_lifecycle_local) and before a disk evict (a kernel
        holding the prefix open must not outlive its realization). Returns the
        number of sessions shut down; in-memory state in those kernels is gone
        by design — callers must SAY so in their result note."""
        # EXACT tail match, not substring: scope keys put the env name LAST
        # (`{thread}::env::{name}`, `{thread}@{site}::env::{name}`), so a
        # substring `marker in k[0]` test would evict env `foo`'s kernels when
        # asked to evict env `fo` — any name that is a prefix of another's
        # (`data` vs `dataset`), destroying unrelated in-memory state. The name is
        # always terminal, so endswith is the correct exact predicate.
        marker = f"::env::{env_name}"
        n = 0
        with self._lock:
            for key in [k for k in self._sessions if k[0].endswith(marker)]:
                try:
                    self._sessions.pop(key).shutdown()
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
        return n

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
        # atexit covers graceful interpreter exit (tests, Ctrl-C dev); the
        # FastAPI shutdown lifecycle calls shutdown_all() for uvicorn graceful
        # restarts. Weft kernels are substrate-owned (no local OS pids), so the
        # legacy-era pid reaper/SIGKILL machinery is gone with that transport —
        # kernel_stop through the substrate is the cleanup.
        import atexit
        atexit.register(_POOL.shutdown_all)
    return _POOL
