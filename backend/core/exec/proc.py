"""Cancellable subprocess runner for installs (pip / micromamba / R).

Long installs run synchronously inside a tool call; without this, a Stop can't
abort them. Launch in a new process group and register a killpg interrupter on
the cancel token so Stop terminates the whole tree (pip/conda spawn children).
Returns a CompletedProcess (never raises on non-zero — callers inspect it).
"""
from __future__ import annotations
import os
import signal
import subprocess
from typing import Optional, Sequence


def run_cancellable(cmd: Sequence[str], *, env: Optional[dict] = None,
                    timeout_s: int = 1800, cancel_token=None) -> subprocess.CompletedProcess:
    if cancel_token is None:
        return subprocess.run(list(cmd), capture_output=True, text=True,
                              env=env, timeout=timeout_s)
    proc = subprocess.Popen(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)

    def _kill():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass

    unregister = cancel_token.register(_kill)
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill()
        out, err = proc.communicate()
    finally:
        unregister()
    return subprocess.CompletedProcess(list(cmd), proc.returncode, out, err)
