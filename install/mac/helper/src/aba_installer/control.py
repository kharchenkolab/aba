"""Control API — install/update/start/stop/status/logs/uninstall.

Long-running operations (install, update) stream progress as Server-Sent
Events so the UI can render a step-by-step progress view without
polling.

A single "operation lock" prevents two long ops from running at once.
"""
from __future__ import annotations
import asyncio
import json
import os
import queue
import shutil
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from aba_installer.paths import aba_home, env_dir, repo_dir, logs_dir, installer_dir
from aba_installer.playbook import Executor, load_playbook


router = APIRouter(prefix="/api", tags=["control"])


# ─── operation state ───────────────────────────────────────────────────────
@dataclass
class OperationState:
    """Tracks the currently-running long operation (install or update).
    Only one can run at a time; trying to start a second 409s out."""
    name: Optional[str] = None        # 'install' | 'update' | None
    started_at: Optional[float] = None
    progress: list[dict] = None       # event stream replay for late subscribers

    def __post_init__(self):
        if self.progress is None:
            self.progress = []


_op_state = OperationState()
_op_lock = threading.Lock()


# ─── playbook locator ──────────────────────────────────────────────────────
def _playbook_path(name: str) -> Path:
    """Map a playbook name → bundled YAML path. Caller validates name."""
    here = Path(__file__).resolve().parent
    return here / f"{name}.yml"


# ─── SSE helpers ───────────────────────────────────────────────────────────
def _sse_format(event_name: str, payload: dict) -> bytes:
    """Encode one event as a Server-Sent-Events frame.

    Format:  event: <name>\\ndata: <json>\\n\\n
    """
    line = f"event: {event_name}\ndata: {json.dumps(payload)}\n\n"
    return line.encode("utf-8")


def _run_playbook_in_background(name: str) -> queue.Queue:
    """Start the playbook in a worker thread; return a queue of events the
    HTTP handler can stream as they arrive."""
    pb_path = _playbook_path(name)
    if not pb_path.exists():
        raise HTTPException(status_code=400, detail=f"unknown playbook: {name}")
    pb = load_playbook(pb_path)

    q: queue.Queue = queue.Queue()

    def on_event(ev_name: str, payload: dict) -> None:
        with _op_lock:
            _op_state.progress.append({"event": ev_name, "payload": payload})
        q.put(("event", ev_name, payload))

    def worker():
        try:
            ex = Executor(pb, on_event=on_event)
            results = ex.run_all()
            ok = all(r.ok for r in results)
            error = next((r.error for r in results if r.error), None)
            q.put(("done", "complete", {"ok": ok, "error": error,
                                        "step_count": len(results)}))
        except Exception as e:
            q.put(("done", "error", {"ok": False, "error": str(e)}))
        finally:
            with _op_lock:
                _op_state.name = None
                _op_state.started_at = None

    t = threading.Thread(target=worker, daemon=True, name=f"aba-{name}")
    t.start()
    return q


async def _drain_queue_as_sse(q: queue.Queue) -> AsyncIterator[bytes]:
    """Yield SSE frames from the operation worker's queue until it signals
    done. Uses asyncio.to_thread for the blocking .get() so the HTTP
    handler stays cooperative."""
    while True:
        item = await asyncio.to_thread(q.get)
        kind = item[0]
        if kind == "event":
            _, name, payload = item
            yield _sse_format(name, payload)
        elif kind == "done":
            _, name, payload = item
            yield _sse_format(name, payload)
            return


# ─── install / update ──────────────────────────────────────────────────────
def _start_op(name: str) -> queue.Queue:
    import time
    with _op_lock:
        if _op_state.name is not None:
            raise HTTPException(status_code=409,
                                detail=f"already running: {_op_state.name}")
        _op_state.name = name
        _op_state.started_at = time.time()
        _op_state.progress = []   # fresh replay buffer
    return _run_playbook_in_background(name)


@router.post("/install")
async def install():
    q = _start_op("install")
    return StreamingResponse(_drain_queue_as_sse(q), media_type="text/event-stream")


@router.post("/update")
async def update():
    """Pull latest aba + aba-recipes, refresh env, rebuild frontend, bounce
    backend. Surfaced as the UI's "Check for updates" button."""
    q = _start_op("update")
    return StreamingResponse(_drain_queue_as_sse(q), media_type="text/event-stream")


@router.get("/operation")
def current_operation() -> dict:
    """The currently-running operation, if any. For UIs that loaded after the
    SSE stream already started — they can poll this to know what's in
    flight and replay the event buffer."""
    with _op_lock:
        return {
            "name": _op_state.name,
            "started_at": _op_state.started_at,
            "events": list(_op_state.progress[-200:]),  # last 200 events
        }


# ─── start / stop / status / logs ──────────────────────────────────────────
def _aba_launcher() -> Optional[Path]:
    """Find the 'aba' launcher. Prefers ~/bin/aba (default install location);
    falls back to /usr/local/bin/aba (admin install)."""
    home_bin = Path.home() / "bin" / "aba"
    if home_bin.exists():
        return home_bin
    sysbin = Path("/usr/local/bin/aba")
    if sysbin.exists():
        return sysbin
    return None


def _backend_pid() -> Optional[int]:
    """Heuristic: find a uvicorn main:app process owned by this user. Robust
    enough for the helper's purposes — it doesn't share a port with anything
    else by design."""
    try:
        proc = subprocess.run(
            ["pgrep", "-f", "uvicorn.*main:app"],
            capture_output=True, text=True, check=False, timeout=5,
        )
        out = (proc.stdout or "").strip()
        if not out:
            return None
        # First match
        return int(out.splitlines()[0])
    except Exception:
        return None


@router.post("/start")
def start_backend() -> dict:
    """Start the ABA backend via the aba launcher. No-op if already running."""
    if _backend_pid() is not None:
        return {"ok": True, "already_running": True}
    launcher = _aba_launcher()
    if launcher is None:
        raise HTTPException(status_code=409, detail="aba launcher not installed yet — run /install first")
    proc = subprocess.run([str(launcher), "up"], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise HTTPException(status_code=500, detail=proc.stderr.strip() or "aba up failed")
    return {"ok": True, "stdout": proc.stdout.strip()}


@router.post("/stop")
def stop_backend() -> dict:
    """Stop the ABA backend."""
    launcher = _aba_launcher()
    if launcher is not None:
        proc = subprocess.run([str(launcher), "stop"], capture_output=True, text=True, timeout=30)
        return {"ok": True, "stdout": proc.stdout.strip()}
    # Fallback: signal directly
    pid = _backend_pid()
    if pid is None:
        return {"ok": True, "already_stopped": True}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    return {"ok": True, "killed": pid}


@router.get("/status")
def status() -> dict:
    """Comprehensive status the Control page reads on load + periodically."""
    home = aba_home()
    installed = env_dir().exists() and (repo_dir() / "aba").exists()
    pid = _backend_pid()
    with _op_lock:
        op = _op_state.name
    config_present = (home / "config.env").exists()
    return {
        "aba_home": str(home),
        "installed": installed,
        "backend_running": pid is not None,
        "backend_pid": pid,
        "operation": op,
        "credentials": config_present,
        "launcher": str(_aba_launcher()) if _aba_launcher() else None,
    }


@router.get("/logs")
def logs(tail: int = 200) -> dict:
    """Recent backend log lines. UI displays them when the user clicks
    'Show logs' or troubleshoots a failed start."""
    if tail < 0 or tail > 5000:
        raise HTTPException(status_code=400, detail="tail must be 0..5000")
    log_path = logs_dir() / "backend.log"
    if not log_path.exists():
        return {"path": str(log_path), "lines": []}
    try:
        text = log_path.read_text(errors="replace").splitlines()
        return {"path": str(log_path), "lines": text[-tail:] if tail else text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── uninstall ─────────────────────────────────────────────────────────────
@router.post("/uninstall")
def uninstall(keep_runtime: bool = True) -> dict:
    """Remove ABA from this Mac. By default keeps the runtime dir (projects,
    data) so the user can re-install later without losing work."""
    home = aba_home()
    # Stop backend first
    try:
        stop_backend()
    except HTTPException:
        pass

    # Remove the launcher
    for p in (Path.home() / "bin" / "aba", Path("/usr/local/bin/aba")):
        try:
            if p.exists() and p.is_file():
                p.unlink()
        except PermissionError:
            pass  # /usr/local/bin needs sudo — silently skip, user can clean up

    # Remove env + repo; keep runtime + config unless keep_runtime=False
    removed = []
    for sub in ("env", "repo", "installer", "logs"):
        target = home / sub
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(sub)
    if not keep_runtime:
        for sub in ("runtime", "config.env"):
            target = home / sub
            if target.exists():
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink()
                removed.append(sub)

    return {"ok": True, "removed": removed, "kept_runtime": keep_runtime}
