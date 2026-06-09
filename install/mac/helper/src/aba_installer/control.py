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


# ─── install artifacts the shell playbook can't render itself ───────────────
def prepare_install_artifacts() -> Path:
    """Render the `aba` launcher into $ABA_HOME/installer/aba.

    The launcher needs ABA_HOME/env/repo/port baked in by Python
    substitution (see launcher.py), which the pure-shell playbook can't do
    — its install-launcher step just copies this rendered file to
    $ABA_HOME/bin/aba. Called before the playbook runs. Idempotent.
    """
    from aba_installer import launcher
    dest = installer_dir() / "aba"
    dest.write_text(launcher.render(launcher.default_context()))
    os.chmod(dest, 0o755)
    _write_deployment_yaml()
    return dest


def _write_deployment_yaml() -> None:
    """Register the cloned recipe library as a content layer.

    The backend reads $ABA_HOME/deployment.yaml (core/config_layers.py) to
    find overlay content; without this file the cloned aba-recipes sits on
    disk but never loads. A layer whose path is missing is skipped silently,
    so writing this unconditionally is safe even before the clone lands.
    """
    recipes = repo_dir() / "aba-recipes"
    (aba_home() / "deployment.yaml").write_text(
        "# Auto-written by the ABA installer. Points the backend at the\n"
        "# cloned recipe library (see core/config_layers.py).\n"
        "layers:\n"
        "  - name: aba-recipes\n"
        f"    path: {recipes}\n"
    )


# ─── background install: everything that doesn't need credentials ───────────
# The whole install EXCEPT the final backend start needs no API key, so it runs
# automatically the moment the UI loads — in parallel with the user signing in.
# That's why there's no "Install ABA" button: by the time auth finishes, the
# install is done (or nearly), and the backend just starts. Only start-backend
# is credential-gated (it boots uvicorn with the key from config.env).
_BG_SKIP = {"start-backend"}
_bg_lock = threading.Lock()
# step_status: {step_id -> "active"|"ok"|"fail"} — survives the event-buffer
# eviction so the UI's checklist doesn't lose its checkmarks when create-env's
# command_output flood pushes the early step_start/step_end frames off the buffer.
_bg: dict = {"thread": None, "events": [], "status": "idle",  # idle|running|done|error
             "step_status": {}}


def _is_installed() -> bool:
    """A *runnable* install: a usable env (uvicorn), a built frontend, and the
    launcher. Not just 'dirs exist' — those appear mid-build."""
    return (
        (env_dir() / "bin" / "uvicorn").exists()
        and (repo_dir() / "aba" / "frontend" / "dist" / "index.html").exists()
        and (aba_home() / "bin" / "aba").exists()
    )


def _agent_repair_enabled() -> bool:
    return os.environ.get("ABA_INSTALL_AGENT_REPAIR", "").lower() in ("1", "true", "yes", "on")


def _repair_hook(on_event):
    """Tier-0 agent-repair hook for the Executor, or None. Active only when
    ABA_INSTALL_AGENT_REPAIR is set AND a `claude` binary is available; on a step
    failure it asks Claude Code to fix the system, then the step is retried."""
    if not _agent_repair_enabled():
        return None
    try:
        from .agent_repair import make_repair_hook
    except Exception:  # noqa: BLE001
        return None
    # ensure=True: the `claude` CLI is bootstrapped on the FIRST step failure
    # (not on the happy path), then used to repair + retry.
    return make_repair_hook(cwd=os.environ.get("ABA_HOME"), on_event=on_event, ensure=True)


def _run_preflight_if_enabled(pb, on_event) -> None:
    """When agent repair is enabled, run a proactive pre-flight BEFORE the
    playbook: probe the system + pre-fix known blockers. Best-effort; never
    blocks the install (errors are surfaced, not raised)."""
    if not _agent_repair_enabled():
        return
    try:
        from .agent_repair import ensure_claude, run_preflight
        claude = ensure_claude(on_event=on_event)
        if not claude:
            return
        plan = "; ".join(f"{s.id}: {s.title}" for s in pb.steps)
        run_preflight(plan, cwd=os.environ.get("ABA_HOME"), claude=claude, on_event=on_event)
    except Exception as e:  # noqa: BLE001
        on_event("repair", {"phase": "error", "message": f"pre-flight error: {e}"})


def _bg_worker() -> None:
    pb = load_playbook(_playbook_path("install"))
    steps = [s.id for s in pb.steps if s.id not in _BG_SKIP]

    def on_event(ev: str, payload: dict) -> None:
        with _bg_lock:
            _bg["events"].append({"event": ev, "payload": payload})
            if len(_bg["events"]) > 1000:
                del _bg["events"][:-1000]
            # Step status survives eviction (the UI's checklist depends on it).
            sid = payload.get("step_id") if isinstance(payload, dict) else None
            if sid:
                if ev == "step_start":
                    _bg["step_status"][sid] = "active"
                elif ev == "step_end":
                    _bg["step_status"][sid] = "ok" if payload.get("ok") else "fail"

    try:
        prepare_install_artifacts()
        # Tell the UI the FULL planned list up front so it can render a fixed
        # checklist (○ → ✓) instead of revealing steps one at a time. Sent as the
        # first event so the UI's event-replay sees it; /api/install/auto also
        # exposes the same list as a top-level field for late subscribers.
        on_event("step_planned", {
            "steps": [{"id": s.id, "title": s.title}
                      for s in pb.steps if s.id not in _BG_SKIP]
        })
        _run_preflight_if_enabled(pb, on_event)
        results = Executor(pb, on_event=on_event,
                           on_step_failed=_repair_hook(on_event)).run_all(only=set(steps))
        ok = bool(results) and all(r.ok for r in results)
        with _bg_lock:
            _bg["status"] = "done" if ok else "error"
    except Exception as e:  # noqa: BLE001
        with _bg_lock:
            _bg["status"] = "error"
            _bg["events"].append({"event": "error", "payload": {"error": str(e)}})


@router.post("/install/auto")
def install_auto() -> dict:
    """Start (or continue) the background install. Idempotent — a no-op if it's
    already running or the install is already complete."""
    with _bg_lock:
        t = _bg["thread"]
        if t is not None and t.is_alive():
            return {"started": False, "status": "running"}
        if _is_installed():
            _bg["status"] = "done"
            return {"started": False, "status": "done"}
        _bg["events"] = []
        _bg["step_status"] = {}
        _bg["status"] = "running"
        th = threading.Thread(target=_bg_worker, daemon=True, name="aba-bg-install")
        _bg["thread"] = th
        th.start()
    return {"started": True, "status": "running"}


@router.get("/install/auto")
def install_auto_status() -> dict:
    # Total + planned steps so the UI can render a fixed checklist up-front
    # (one row per top-level category, marked ✓ as each finishes) instead of
    # revealing them one-by-one. Computed fresh per call — robust to the event
    # buffer evicting the step_planned frame on long installs.
    try:
        pb = load_playbook(_playbook_path("install"))
        active = [s for s in pb.steps if s.id not in _BG_SKIP]
        steps = [{"id": s.id, "title": s.title} for s in active]
    except Exception:  # noqa: BLE001
        steps = []
    with _bg_lock:
        return {"status": _bg["status"], "total_steps": len(steps),
                "steps": steps,
                # Authoritative per-step state — UI uses this to draw the
                # checklist instead of inferring from events, so checkmarks
                # don't blink off when create-env evicts old step events.
                "step_status": dict(_bg["step_status"]),
                "events": list(_bg["events"][-300:])}


def _await_background(emit) -> None:
    """If the background install is in flight, wait for it before an explicit
    /api/install runs the same steps. Surfaces the wait so the UI isn't silent."""
    with _bg_lock:
        t = _bg["thread"]
    if t is not None and t.is_alive():
        emit("step_start", {"step_id": "bg-wait", "title": "Finishing background setup…"})
        t.join()
        emit("step_end", {"step_id": "bg-wait", "ok": True, "error": None})


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
            # command_output can be thousands of lines — keep the replay buffer bounded.
            if len(_op_state.progress) > 500:
                del _op_state.progress[:-500]
        q.put(("event", ev_name, payload))

    def worker():
        try:
            # Render Python-substituted artifacts (the launcher) before the
            # shell playbook reaches the step that installs them.
            prepare_install_artifacts()
            # If the background install is in flight, wait for it so steps
            # don't run twice on the same prefix.
            if name == "install":
                _await_background(on_event)
            # Tell the UI the planned checklist up front (same shape the auto
            # path emits — see _bg_worker).
            on_event("step_planned", {
                "steps": [{"id": s.id, "title": s.title} for s in pb.steps]
            })
            _run_preflight_if_enabled(pb, on_event)
            ex = Executor(pb, on_event=on_event, on_step_failed=_repair_hook(on_event))
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
    try:
        return _run_playbook_in_background(name)
    except Exception:
        # The worker's finally-clause clears the lock, but if we never got
        # that far (e.g. unknown playbook 400s), clear it here so a failed
        # start doesn't wedge every future op behind a phantom 409.
        with _op_lock:
            _op_state.name = None
            _op_state.started_at = None
        raise


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
    """Find the 'aba' launcher. Prefers $ABA_HOME/bin/aba (the self-contained
    default), then the rendered installer copy, then legacy ~/bin /
    /usr/local/bin locations from older installs."""
    for p in (aba_home() / "bin" / "aba",
              installer_dir() / "aba",
              Path.home() / "bin" / "aba",
              Path("/usr/local/bin/aba")):
        if p.exists() and p.is_file():
            return p
    return None


def _backend_pid() -> Optional[int]:
    """Find THIS install's backend by its unique --app-dir. Scoped (not a bare
    'uvicorn main:app') so a stale process from a crashed/old install doesn't
    read as running — which would make /api/start no-op into a dead port."""
    app_dir = str(repo_dir() / "aba" / "backend")
    try:
        proc = subprocess.run(
            ["pgrep", "-f", f"uvicorn main:app --app-dir {app_dir}"],
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
    installed = _is_installed()
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

    # Unload + remove the auto-start LaunchAgent. Without this, deleting
    # installer/ (below) leaves launchd pointing at a now-missing helper
    # venv, which it then tries to start on every login.
    launchagent_removed = False
    try:
        from aba_installer.launchagent import uninstall_launch_agent
        launchagent_removed = uninstall_launch_agent()
    except Exception:
        pass

    # Clear any legacy launcher copies from older installs (the current one
    # lives under $ABA_HOME/bin and is removed with the "bin" subdir below).
    for p in (Path.home() / "bin" / "aba", Path("/usr/local/bin/aba")):
        try:
            if p.exists() and p.is_file():
                p.unlink()
        except PermissionError:
            pass  # /usr/local/bin needs sudo — silently skip, user can clean up

    # Remove env + repo + the launcher/micromamba bin; keep runtime + config
    # unless keep_runtime=False.
    removed = []
    for sub in ("env", "repo", "installer", "logs", "bin"):
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

    if launchagent_removed:
        removed.append("launchagent")
    return {"ok": True, "removed": removed, "kept_runtime": keep_runtime}
