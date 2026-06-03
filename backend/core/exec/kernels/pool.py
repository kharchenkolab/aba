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

    def owned_kernel_pids(self) -> list[int]:
        """OS pids of kernels this pool currently owns. Used by the
        SIGTERM/SIGINT handler to hard-kill them before uvicorn forcibly
        exits the worker (atexit doesn't fire on SIGKILL/SIGTERM)."""
        out: list[int] = []
        with self._lock:
            for s in list(self._sessions.values()):
                pid = getattr(s, "kernel_pid", lambda: None)()
                if isinstance(pid, int) and pid > 0:
                    out.append(pid)
        return out

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
        # Two-layer cleanup (PK 2026-06-03 — uvicorn bounces were leaking
        # ~10 multi-day orphan kernels, ~15 GB resident):
        #   - atexit: covers graceful interpreter exit (tests, Ctrl-C dev).
        #     Doesn't fire on SIGTERM/SIGKILL — uvicorn workers spawned via
        #     multiprocessing.spawn don't reliably receive the parent's
        #     signals, AND `signal.signal()` from a non-main thread raises.
        #     The proper shutdown path for uvicorn is the FastAPI shutdown
        #     lifecycle (registered in main.py's @app.on_event("shutdown")
        #     handler), which DOES fire on graceful restart + reload.
        #   - Startup orphan reaper: SIGKILLs kernels that look like they
        #     came from prior aba runs but have parent PID 1 (reparented
        #     to init when the owning uvicorn died). Catches survivors
        #     from forced-kill scenarios where shutdown didn't run.
        import atexit
        atexit.register(_POOL.shutdown_all)
        _reap_orphan_kernels()
    return _POOL


def _reap_orphan_kernels() -> int:
    """Find and SIGKILL kernel processes left behind by prior uvicorn runs.

    A kernel is an orphan iff its ancestor chain reaches PID 1 (init)
    WITHOUT passing through the currently-running uvicorn process tree.
    Two common shapes:
      - Direct orphan: kernel's PPID is 1 (immediate reparent on its
        owner's death).
      - Zombie-worker orphan: kernel's parent is a dead `multiprocessing
        spawn_main` worker that's itself reparented to init — the
        situation observed when uvicorn's worker dies but its kernel
        child + the worker shell linger as PPID=1 zombies (PK 2026-06-03).

    Conservative scoping:
      - Only kernels matching our launch shape (R IRkernel or
        python ipykernel_launcher).
      - Only processes owned by our uid.
      - Only kernels whose cmdline references a /tmp/<...>.json
        connection file that's still on disk (so we don't shoot
        anything we can't prove was ours).
    """
    import os, signal, glob
    killed = 0
    my_uid = os.getuid()
    my_pid = os.getpid()
    # Connection-file horizon — limit to files in dirs where jupyter typically
    # drops them on this system.
    candidate_dirs = ["/tmp"] + glob.glob("/tmp/claude-*") + glob.glob("/tmp/jupyter*")
    seen_conn_files: set[str] = set()
    for d in candidate_dirs:
        for pat in ("tmp*.json", "kernel-*.json"):
            for f in glob.glob(os.path.join(d, pat)):
                seen_conn_files.add(f)

    def _ppid_of(pid: int) -> int:
        try:
            with open(f"/proc/{pid}/status") as fh:
                for ln in fh:
                    if ln.startswith("PPid:"):
                        return int(ln.split()[1])
        except (FileNotFoundError, PermissionError, ValueError):
            pass
        return -1

    def _is_orphan_chain(pid: int) -> bool:
        """Walk ppid chain. Return True iff we hit PID 1 (init) without
        encountering my_pid first. Caps depth to avoid runaway loops."""
        cur = pid
        for _ in range(20):
            ppid = _ppid_of(cur)
            if ppid <= 0:
                return False
            if ppid == my_pid:
                return False        # mine — not an orphan
            if ppid == 1:
                return True         # hit init without finding me → orphan
            cur = ppid
        return False                # loop safety — don't kill

    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                st = os.stat(f"/proc/{pid}")
                if st.st_uid != my_uid:
                    continue
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    cmd = fh.read().decode("utf-8", errors="replace")
                if "IRkernel::main" not in cmd and "ipykernel_launcher" not in cmd:
                    continue
                if not any(cf in cmd for cf in seen_conn_files):
                    continue        # connection file gone — can't prove ours
                if not _is_orphan_chain(pid):
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
                    print(f"[kernel-reaper] killed orphan kernel pid={pid}", flush=True)
                except (ProcessLookupError, PermissionError):
                    pass
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
    except FileNotFoundError:
        return 0  # /proc unavailable (non-Linux)
    if killed:
        print(f"[kernel-reaper] reaped {killed} orphan kernels at startup", flush=True)
    return killed
