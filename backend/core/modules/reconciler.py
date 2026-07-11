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
    return _repo_aba_root() / spec.install_script


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
        log(f"[modules] {spec.id}: installing → {_log_path(spec)}")
        rc = runner(["bash", str(script)], _module_env(spec), _log_path(spec))
        if rc == 0 and manager.probe_ready(spec):
            state.set_status(spec.id, "idle")
            log(f"[modules] {spec.id}: ready")
            return True
        err = f"install script exited {rc}" + ("" if rc else " but capability not detected")
        state.set_status(spec.id, "failed", error=err)
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


def ensure_module(module_id: str, *, log: Callable[[str], None] = print) -> dict | None:
    """First-use / manual enable: persist desired=enabled and, if not already
    ready/installing, launch this module's install in a background thread. Returns the
    module view (with the freshly-updated status) or None for an unknown id."""
    spec = registry.get(module_id)
    if spec is None:
        return None
    state.set_desired(module_id, "enabled")
    if manager.actual_state(spec) not in ("ready", "installing"):
        state.set_status(module_id, "queued")

        def _work():
            run_module(spec, log=log)

        threading.Thread(target=_work, name=f"module_install_{module_id}", daemon=True).start()
    return manager.get_view(module_id)
