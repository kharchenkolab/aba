"""Cancellable + progress-streaming subprocess runner for installs.

Long installs (pip / micromamba / R compiles) run synchronously inside a tool
call. Two things this gives them:
  - **Cancellable**: launch in a new process group + register a killpg
    interrupter on the cancel token, so Stop terminates the whole tree.
  - **Live**: stream the subprocess's output line-by-line into the
    `tool_progress` channel (milestone-filtered so it's a readable trickle, not
    a flood) — "Solving… Downloading nextflow… Linking…" as it happens.

Returns a CompletedProcess (combined output in `stdout`; never raises) so
callers inspect returncode/output as before.
"""
from __future__ import annotations
import os
import re
import signal
import subprocess
import threading
from typing import Optional, Sequence

# Milestone lines worth surfacing live across pip / conda / R / nextflow — keeps
# the trickle meaningful and bounded (the full log still goes to the result).
_MILESTONE = re.compile(
    r"Solving|Downloading|Extracting|Linking|Preparing|Transaction|"
    r"Collecting|Building wheel|Installing|Successfully installed|"
    r"\* installing|^\*\* |DONE \(|trying URL|^downloaded |"
    r"N E X T F L O W|Pulling|executor >|process >|Submitted process|Completed|"
    r"error|Error|Warning|warning:", re.I)


def run_cancellable(cmd: Sequence[str], *, env: Optional[dict] = None,
                    timeout_s: int = 1800, cancel_token=None,
                    stream: bool = True) -> subprocess.CompletedProcess:
    from core.runtime import progress  # lazy; emit is a no-op without a sink
    proc = subprocess.Popen(
        list(cmd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env=env, start_new_session=True,
    )

    def _kill():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            pass

    unregister = cancel_token.register(_kill) if cancel_token is not None else None
    timer = threading.Timer(timeout_s, _kill)   # hard deadline even if output stalls
    timer.start()
    lines: list[str] = []
    try:
        for line in proc.stdout:                # arrives as the process emits it
            lines.append(line)
            if stream:
                s = line.rstrip()
                if s and _MILESTONE.search(s):
                    progress.emit(s[:200], phase="run")
        proc.wait()
    finally:
        timer.cancel()
        if unregister is not None:
            unregister()
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
    return subprocess.CompletedProcess(list(cmd), proc.returncode, "".join(lines), "")
