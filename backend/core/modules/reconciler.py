"""Module reconciler — the backend owns post-install (misc/modules.md).

On startup (and on demand) it compares desired-vs-actual and installs every module
that is enabled-but-not-ready by running that module's install script (the SAME
scripts the installer uses — no duplicated logic). Installs run SERIALLY in a daemon
thread to avoid disk/CPU thrash, streaming status to $ABA_HOME/modules.json and a
per-module log. This replaces the installer playbook's post-`start-backend`
`complete-*-env` steps: the installer just brings the server up; capability fills in
here, visible in Settings → Modules.
"""
from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Callable

from core.modules import manager, registry, state
from core.modules.registry import ModuleSpec

# Serialize installs + track in-flight ids so a first-use ensure_module() can't
# double-launch a module the boot reconcile is already building.
_LOCK = threading.Lock()
_INFLIGHT: set[str] = set()
_started = False


def _aba_home() -> Path:
    return Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))


def _repo_aba_root() -> Path:
    """The `aba/` checkout root that holds install/ and backend/. Derived from this
    file's location (backend/core/modules/reconciler.py → parents[3] == aba/), with an
    $ABA_HOME/repo/aba fallback for unusual layouts."""
    here = Path(__file__).resolve().parents[3]
    if (here / "install" / "core" / "modules").is_dir():
        return here
    return _aba_home() / "repo" / "aba"


def _script_path(spec: ModuleSpec) -> Path:
    # install_script is an ABSOLUTE path resolved by the registry from the manifest's
    # own directory (forward-compatible with bundle-contributed module dirs).
    return Path(spec.install_script)


def _module_env(spec: ModuleSpec) -> dict[str, str]:
    """Environment for a module script: the process env (launcher already sourced
    config.env) plus the paths the scripts expect."""
    env = dict(os.environ)
    home = _aba_home()
    env.setdefault("ABA_HOME", str(home))
    env.setdefault("ENV_DIR", str(home / "env"))
    env.setdefault("MAMBA", str(home / "bin" / "micromamba"))
    env.setdefault("REPO_DIR", str(_repo_aba_root().parent))   # scripts expect $REPO_DIR/aba/...
    return env


def _log_path(spec: ModuleSpec) -> Path:
    return _aba_home() / "logs" / f"module-{spec.id}.log"


def _default_runner(cmd: list[str], env: dict[str, str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as f:
        p = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT, text=True)
    return p.returncode


Runner = Callable[[list[str], dict, Path], int]


def run_module(spec: ModuleSpec, *, runner: Runner = _default_runner,
               log: Callable[[str], None] = print) -> bool:
    """Install one module by running its script. Records status
    installing→idle/failed. Returns True on success. Never raises."""
    script = _script_path(spec)
    if not script.exists():
        state.set_status(spec.id, "failed", error=f"install script missing: {script}")
        log(f"[modules] {spec.id}: install script missing: {script}")
        return False
    with _LOCK:
        if spec.id in _INFLIGHT:
            log(f"[modules] {spec.id}: already installing — skipping duplicate")
            return False
        _INFLIGHT.add(spec.id)
    try:
        state.set_status(spec.id, "installing", progress=f"running {spec.install_script}", error="")
        _notify(spec, "installing", progress=f"Installing {spec.title}…")
        log(f"[modules] {spec.id}: installing → {_log_path(spec)}")
        rc = runner(["bash", str(script)], _module_env(spec), _log_path(spec))
        if rc == 0 and manager.probe_ready(spec):
            state.set_status(spec.id, "idle")
            _notify(spec, "ready")
            log(f"[modules] {spec.id}: ready")
            return True
        err = f"install script exited {rc}" + ("" if rc else " but capability not detected")
        state.set_status(spec.id, "failed", error=err)
        _notify(spec, "failed", error=err)
        log(f"[modules] {spec.id}: FAILED ({err}) — see {_log_path(spec)}")
        return False
    finally:
        with _LOCK:
            _INFLIGHT.discard(spec.id)


def _pending(spec: ModuleSpec) -> bool:
    """Should the reconciler install this module now? Enabled, and not already
    ready/installing."""
    return manager.is_enabled(spec) and manager.actual_state(spec) not in ("ready", "installing", "queued")


def reconcile(*, runner: Runner = _default_runner, log: Callable[[str], None] = print) -> dict:
    """Install all enabled-but-missing modules, SERIALLY, in registry order. Safe to
    call repeatedly (ready modules are skipped). Returns {id: bool|'skipped'}."""
    results: dict[str, object] = {}
    for spec in registry.all_modules():
        if not manager.is_enabled(spec):
            results[spec.id] = "disabled"
            continue
        if manager.actual_state(spec) == "ready":
            results[spec.id] = "ready"
            continue
        results[spec.id] = run_module(spec, runner=runner, log=log)
    return results


def start(*, log: Callable[[str], None] = print) -> bool:
    """Kick off a one-shot background reconcile in a daemon thread. Idempotent — a
    second call while one is running is a no-op. Returns True if it started a thread."""
    global _started
    with _LOCK:
        if _started:
            return False
        _started = True

    def _work():
        global _started
        try:
            reconcile(log=log)
        finally:
            with _LOCK:
                _started = False

    threading.Thread(target=_work, name="module_reconcile", daemon=True).start()
    return True


def _remove_artifacts(spec: ModuleSpec, log: Callable[[str], None]) -> None:
    """Reclaim a module's on-disk artifacts per its manifest `remove` block
    (misc/modules.md): {paths: [...]} rmtree'd (with $VARs expanded), optional
    {script: ...} run. No per-module Python."""
    import shutil
    rm = spec.remove or {}
    for p in rm.get("paths", []):
        target = manager.expand_path(str(p))
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
            log(f"[modules] {spec.id}: removed {target} (reclaimed disk)")
    if rm.get("script"):
        import subprocess
        subprocess.run(["bash", str(manager.expand_path(str(rm["script"])))], check=False)


def _notify(spec: ModuleSpec, mstate: str, *, progress: str | None = None,
            error: str | None = None) -> None:
    """Push a module state-change onto the global /api/notifications channel so the UI
    can toast + live-refresh. Best-effort; never raises."""
    try:
        from core.runtime.notifications import broadcast
        broadcast({"type": "module", "id": spec.id, "title": spec.title,
                   "state": mstate, "progress": progress, "error": error})
    except Exception:  # noqa: BLE001
        pass


def _kick_install(spec: ModuleSpec, *, log: Callable[[str], None]) -> None:
    """Queue + launch a single module's install in a background thread (no-op if it's
    already ready/installing)."""
    if manager.actual_state(spec) in ("ready", "installing"):
        return
    state.set_status(spec.id, "queued")
    _notify(spec, "queued")

    def _work():
        run_module(spec, log=log)

    threading.Thread(target=_work, name=f"module_install_{spec.id}", daemon=True).start()


def set_mode(module_id: str, new_mode: str, *, remove: bool = False,
             log: Callable[[str], None] = print) -> dict | None:
    """Set a module's state to on | first_use | off (the 3-state control). Effects:
      • on        → persist + install now (background).
      • first_use → persist only (installs when the capability is first used).
      • off       → persist; with remove=True also delete artifacts to reclaim disk
                    (removable modules only).
    Returns the view, or None for an unknown id. Raises ValueError on a bad mode or a
    remove of a non-removable module."""
    spec = registry.get(module_id)
    if spec is None:
        return None
    if new_mode not in registry.STATES:
        raise ValueError(f"bad module state {new_mode!r} (expected one of {registry.STATES})")
    state.set_desired(module_id, new_mode)
    if new_mode == "off":
        if remove:
            if not spec.removable:
                raise ValueError(f"{module_id} is not removable (it lives in the base env)")
            _remove_artifacts(spec, log)
            state.set_status(module_id, "idle")
    elif new_mode == "on":
        _kick_install(spec, log=log)
    return manager.get_view(module_id)


def install_and_wait(module_id: str, *, timeout_s: float = 900.0,
                     on_progress: Callable[[str], None] | None = None,
                     poll_s: float = 2.0) -> tuple[bool, str | None]:
    """Synchronously ensure a module is ready — kick its install (respecting mode) and
    BLOCK until ready/failed/timeout. For callers on a background worker (e.g. the
    pagoda3 viewer prepare job) that want to WAIT with progress and surface a failure
    inline. Returns (ok, error). off → (False, reason) without installing."""
    import time
    spec = registry.get(module_id)
    if spec is None:
        return False, f"unknown module {module_id!r}"
    if manager.actual_state(spec) == "ready":
        return True, None
    if not manager.allows_auto_install(spec):
        return False, f"{spec.title} is turned off (Settings → Modules)."
    if on_progress:
        on_progress(f"Installing {spec.title}…")
    ensure_module(module_id)
    deadline = time.monotonic() + max(0.0, timeout_s)
    while time.monotonic() < deadline:
        st = manager.actual_state(spec)
        if st == "ready":
            return True, None
        if st == "failed":
            return False, state.get_status(module_id).get("error") or f"{spec.title} install failed"
        time.sleep(poll_s)
    return False, f"{spec.title} install timed out after {int(timeout_s)}s"


def ensure_module(module_id: str, *, log: Callable[[str], None] = print) -> dict | None:
    """First-use / retry: install this module NOW if its mode allows auto-install (on
    or first_use) and it isn't ready/installing. Does NOT change the mode. Returns the
    module view, or None for an unknown id. A module set to `off` is left untouched."""
    spec = registry.get(module_id)
    if spec is None:
        return None
    if manager.allows_auto_install(spec):
        _kick_install(spec, log=log)
    return manager.get_view(module_id)
