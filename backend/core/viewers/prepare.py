"""In-process job tracker for preparing external-viewer data stores.

Converting an `.h5ad` → `.lstar.zarr` (or unzipping a native store) can take
seconds — too long to block the launch request. `start()` runs the cache-aware
launcher in a background thread and returns a job id; the launch page polls
`status()` and redirects to the viewer when the store is ready. Deliberately
lightweight and in-process (single-node desktop / OOD): a dict + a lock + one
daemon thread per job, with finished jobs pruned so the map can't grow unbounded.
"""
from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# runner(set_phase) -> object with .url / .set_local_storage / .label (a LaunchResult).
Runner = Callable[[Callable[[str], None]], Any]

_MAX_JOBS = 200
_TTL_SECONDS = 3600.0


@dataclass
class PrepareJob:
    id: str
    status: str = "preparing"                     # preparing | ready | error
    phase: str = "Starting…"
    label: Optional[str] = None
    url: Optional[str] = None
    set_local_storage: Optional[dict] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None


_JOBS: dict[str, PrepareJob] = {}
_LOCK = threading.Lock()


def _prune_locked() -> None:
    if len(_JOBS) <= _MAX_JOBS:
        return
    now = time.time()
    for jid in [j.id for j in _JOBS.values() if j.ended_at and now - j.ended_at > _TTL_SECONDS]:
        _JOBS.pop(jid, None)
    while len(_JOBS) > _MAX_JOBS:                  # still over cap → drop oldest finished
        finished = sorted((j for j in _JOBS.values() if j.ended_at), key=lambda j: j.ended_at or 0)
        if not finished:
            break
        _JOBS.pop(finished[0].id, None)


def start(runner: Runner, label: Optional[str] = None) -> str:
    """Kick off `runner` in a background thread; return the job id immediately.
    `runner` is handed a `set_phase(str)` callback to report human-readable
    progress. Its return value's `.url` / `.set_local_storage` / `.label` become
    the job result; any exception becomes the job error."""
    jid = secrets.token_hex(8)
    with _LOCK:
        _JOBS[jid] = PrepareJob(id=jid, label=label)
        _prune_locked()

    def _set_phase(p: str) -> None:
        with _LOCK:
            j = _JOBS.get(jid)
            if j and j.status == "preparing":
                j.phase = p

    def _work() -> None:
        try:
            res = runner(_set_phase)
            with _LOCK:
                j = _JOBS.get(jid)
                if j:
                    j.url = getattr(res, "url", None)
                    j.set_local_storage = getattr(res, "set_local_storage", None)
                    j.label = getattr(res, "label", None) or j.label
                    j.phase, j.status, j.ended_at = "Ready", "ready", time.time()
        except Exception as e:  # noqa: BLE001
            with _LOCK:
                j = _JOBS.get(jid)
                if j:
                    j.error = str(e) or e.__class__.__name__
                    j.phase, j.status, j.ended_at = "Failed", "error", time.time()

    threading.Thread(target=_work, name=f"prepare-{jid}", daemon=True).start()
    return jid


def status(job_id: str) -> Optional[dict]:
    """Current job state, or None if unknown. `elapsed` is seconds since start."""
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return None
        return {
            "id": j.id, "status": j.status, "phase": j.phase, "label": j.label,
            "url": j.url, "set_local_storage": j.set_local_storage, "error": j.error,
            "elapsed": round((j.ended_at or time.time()) - j.started_at, 1),
        }
