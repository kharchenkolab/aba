"""LocalSubprocessExecutor — runs commands in the base venv on this VM.

The subprocess + process-group cancellation + timeout logic is extracted from
content.bio.tools.run_python so it can be reused (Stage 2 rewires run_python to
call this) and so future executors share the same exec contract. This impl
materializes only the base venv; conda/container/remote materialization are
separate impls behind the same `Executor` protocol (P1+).
"""
from __future__ import annotations
import os
import signal
import subprocess
import sys
from typing import Optional, Sequence

from core.exec.base import Env, ExecResult, Provisioning


class LocalSubprocessExecutor:
    """Executor that runs commands as local subprocesses in the base venv."""

    def materialize(self, prov: Provisioning, scope: str = "system") -> Env:
        if prov is None or prov.is_base():
            return Env(id="base-venv", kind="venv", python=sys.executable)
        # Honest stub: this executor only knows the base venv. The conda
        # satisfier is CondaExecutor (P1); container/remote come later. The
        # signature is frozen, so callers don't change when those land.
        raise NotImplementedError(
            "LocalSubprocessExecutor only materializes the base venv; "
            "conda/container/remote provisioning lands in P1+ (separate executor)."
        )

    def exec(
        self,
        env: Env,
        command: Sequence[str],
        *,
        cwd: str,
        mounts: Sequence[tuple[str, str]] = (),
        cancel_token=None,
        timeout_s: int = 90,
        env_vars: Optional[dict] = None,
    ) -> ExecResult:
        # mounts are a no-op for local execution (the process sees the real
        # filesystem); they matter only for container/remote executors.
        proc_env = os.environ.copy()
        proc_env["MPLBACKEND"] = "Agg"
        # The materialized env's overlay (PYTHONPATH for a pylib overlay, PATH
        # for a conda env's bin) composes with the base process env. PATH /
        # PYTHONPATH are prepended so the overlay wins; other keys are set.
        for k, v in (getattr(env, "env_overlay", None) or {}).items():
            k, v = str(k), str(v)
            if k in ("PATH", "PYTHONPATH") and proc_env.get(k):
                proc_env[k] = v + os.pathsep + proc_env[k]
            else:
                proc_env[k] = v
        if env_vars:
            proc_env.update({str(k): str(v) for k, v in env_vars.items()})

        # start_new_session=True puts the child in its own process group so a
        # cancel kills the whole group (forked numpy/matplotlib helpers too),
        # not just the parent pid.
        proc = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=proc_env, cwd=str(cwd),
            start_new_session=True,
        )

        # Register a kill interrupter fired by token.cancel() on the user's
        # Stop. SIGTERM → 2s grace → SIGKILL, via killpg on the group. The
        # unregister must run after the process exits so a stale callback can't
        # kill a recycled pid.
        unregister = None
        if cancel_token is not None:
            def _kill():
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass  # child already dead
            unregister = cancel_token.register(_kill)

        timed_out = False
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout_s)
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                returncode = -1
                timed_out = True
        finally:
            if unregister is not None:
                unregister()

        cancelled = bool(cancel_token is not None and getattr(cancel_token, "cancelled", False))
        return ExecResult(
            returncode=returncode,
            stdout=stdout or "",
            stderr=stderr or "",
            cancelled=cancelled,
            timed_out=timed_out,
        )
