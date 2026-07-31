"""LocalSubprocessExecutor — runs commands as local subprocesses.

Owns the subprocess + process-group cancellation + timeout contract shared by
the one-shot lanes. It knows only the base venv as a materialization target —
environment provisioning is the substrate's job; callers pass already-resolved
interpreters and this executor just runs them."""
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
        stream: bool = False,
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

        # Guard: an empty interpreter/executable or cwd reaching Popen surfaces as
        # the cryptic `PermissionError: [Errno 13] Permission denied: ''` (hit on a
        # background job, 2026-06-28, prj_0590c5d8 job_06e38348d2). Fail with a
        # DIAGNOSABLE error naming exactly what was empty + the run context, so a
        # recurrence is actionable instead of a mystery.
        _cmd = list(command)
        if not _cmd or not str(_cmd[0]).strip():
            raise ValueError(f"exec: empty interpreter/executable in command={_cmd!r} (cwd={cwd!r})")
        if not str(cwd).strip():
            raise ValueError(f"exec: empty cwd for command={_cmd!r}")
        # start_new_session=True puts the child in its own process group so a
        # cancel kills the whole group (forked numpy/matplotlib helpers too),
        # not just the parent pid.
        proc = subprocess.Popen(
            _cmd,
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
            if stream:
                # Live mode: drain stdout/stderr line-by-line on threads so a
                # long background/Slurm job's progress is tailable AS IT RUNS.
                # A Slurm job's stdout is captured to job.log by `sbatch -o`, but a
                # LOCAL background job's tee goes to the SERVER's stdout, not a
                # per-job file — so also append each line to <cwd>/run.log, which
                # /api/jobs/{id} reads for the live tail. (run.log is overwritten
                # with the canonical formatted version at finalize.) Threads keep
                # the pipes drained → no communicate() deadlock; cancel kills via group.
                live_log = None
                try:
                    live_log = open(os.path.join(str(cwd), "run.log"), "w", buffering=1)
                except Exception:  # noqa: BLE001 — live tail is best-effort
                    live_log = None
                try:
                    stdout, stderr, returncode, timed_out = self._exec_streaming(
                        proc, timeout_s, live_log=live_log)
                finally:
                    if live_log is not None:
                        try:
                            live_log.close()
                        except Exception:  # noqa: BLE001
                            pass
            else:
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

    @staticmethod
    def _exec_streaming(proc, timeout_s, live_log=None):
        """Drain proc.stdout/stderr on daemon threads, tee'ing each line to this
        process's stdout/stderr (live, flushed) while accumulating for the
        result. When `live_log` is given, ALSO append each line to it (a per-job
        run.log) so a LOCAL background job — whose tee otherwise reaches only the
        server's stdout — is tailable live via /api/jobs/{id}. Returns
        (stdout, stderr, returncode, timed_out). Used when `stream=True`."""
        import threading

        out_chunks: list[str] = []
        err_chunks: list[str] = []
        _log_lock = threading.Lock()  # stdout+stderr pumps share live_log

        def _pump(src, sink, chunks):
            try:
                for line in iter(src.readline, ""):
                    chunks.append(line)
                    try:
                        sink.write(line)
                        sink.flush()
                    except Exception:  # noqa: BLE001 — tee is best-effort
                        pass
                    if live_log is not None:
                        try:
                            with _log_lock:
                                live_log.write(line)
                                live_log.flush()
                        except Exception:  # noqa: BLE001 — live tail is best-effort
                            pass
            finally:
                try:
                    src.close()
                except Exception:  # noqa: BLE001
                    pass

        t_out = threading.Thread(target=_pump, args=(proc.stdout, sys.stdout, out_chunks), daemon=True)
        t_err = threading.Thread(target=_pump, args=(proc.stderr, sys.stderr, err_chunks), daemon=True)
        t_out.start()
        t_err.start()
        timed_out = False
        try:
            returncode = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                returncode = proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                returncode = -1
            timed_out = True
        # Let the pumps flush the tail the child wrote before exiting.
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        return "".join(out_chunks), "".join(err_chunks), returncode, timed_out
