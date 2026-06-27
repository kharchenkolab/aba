"""Playbook parser + step executor.

A playbook is a YAML document describing the deterministic install
sequence. The executor runs steps in order, capturing stdout/stderr and
exit codes; results stream back to the API caller via an
event-callback so the UI can render progress in real time.

Step shape (see install.yml):
  - id: <stable-string-id>
    title: <human title for the UI>
    why:   <one-paragraph rationale, shown on hover or in "show details">
    commands: [<shell command>, ...]
    timeout_seconds: <int, default 300>
"""
from __future__ import annotations
import os
import queue
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import yaml


# ─── data classes ───────────────────────────────────────────────────────────
@dataclass
class Step:
    id: str
    title: str
    why: str
    commands: list[str]
    timeout_seconds: int = 300
    remediation: str = ""        # shown to the user when the step fails (no-agent robustness)

    @classmethod
    def from_dict(cls, d: dict, *, default_timeout: int = 300) -> "Step":
        cmds = d.get("commands") or []
        if not isinstance(cmds, list):
            raise ValueError(f"step {d.get('id')}: commands must be a list")
        return cls(
            id=str(d["id"]),
            title=str(d.get("title", d["id"])),
            why=str(d.get("why", "")).strip(),
            commands=[str(c) for c in cmds],
            timeout_seconds=int(d.get("timeout_seconds", default_timeout)),
            remediation=str(d.get("remediation", "")).strip(),
        )


@dataclass
class Playbook:
    steps: list[Step]
    env_vars: dict[str, str] = field(default_factory=dict)

    def step(self, sid: str) -> Optional[Step]:
        return next((s for s in self.steps if s.id == sid), None)


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class StepResult:
    step_id: str
    started_at: float
    finished_at: float
    commands: list[CommandResult] = field(default_factory=list)
    error: Optional[str] = None  # populated if a command failed

    @property
    def ok(self) -> bool:
        return self.error is None and all(c.ok for c in self.commands)


# ─── parser ────────────────────────────────────────────────────────────────
def load_playbook(path: Path) -> Playbook:
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"playbook root must be a mapping; got {type(raw).__name__}")
    defaults = raw.get("defaults") or {}
    default_timeout = int(defaults.get("timeout_seconds", 300))
    raw_steps = raw.get("steps") or []
    if not isinstance(raw_steps, list):
        raise ValueError("steps must be a list")
    steps = [Step.from_dict(d, default_timeout=default_timeout) for d in raw_steps]
    env_vars = {str(k): str(v) for k, v in (raw.get("env_vars") or {}).items()}
    return Playbook(steps=steps, env_vars=env_vars)


# ─── executor ──────────────────────────────────────────────────────────────
EventCallback = Callable[[str, dict], None]
"""(event_name, payload) — used by the executor to stream progress.

Events:
  step_start  {step_id, title}
  command_start  {step_id, command}
  command_output {step_id, line}   — one line of live stdout/stderr
  command_end    {step_id, command, exit_code, duration_s, ok}
  step_end       {step_id, ok, error}
"""


class Executor:
    """Runs a playbook (or a subset of steps). Each command runs in a fresh
    shell with the playbook's env_vars exported. Output is captured;
    progress events stream back to the caller."""

    def __init__(self, playbook: Playbook, *,
                 on_event: Optional[EventCallback] = None,
                 base_env: Optional[dict[str, str]] = None,
                 cwd: Optional[Path] = None,
                 on_step_failed=None,
                 max_repair_attempts: int = 1):
        self.playbook = playbook
        self._on_event = on_event or (lambda name, payload: None)
        self._base_env = base_env if base_env is not None else dict(os.environ)
        self._cwd = str(cwd) if cwd is not None else None
        # on_step_failed(step, result, attempt) -> bool: attempt an out-of-band
        # repair (Tier-0 agent), return True to retry the step. None = no repair
        # (exactly the legacy behaviour). max_repair_attempts caps the retries.
        self._on_step_failed = on_step_failed
        self._max_repair_attempts = max_repair_attempts

    # ─── public API ────────────────────────────────────────────────────────
    def run_step(self, step: Step) -> StepResult:
        started = time.monotonic()
        self._on_event("step_start", {"step_id": step.id, "title": step.title})
        env = self._materialize_env()
        result = StepResult(step_id=step.id, started_at=started, finished_at=started)
        for cmd in step.commands:
            self._on_event("command_start", {"step_id": step.id, "command": cmd})

            def _emit_line(line: str, _sid=step.id) -> None:
                self._on_event("command_output", {"step_id": _sid, "line": line})

            cmd_result = self._run_one(cmd, env=env, timeout=step.timeout_seconds,
                                       on_line=_emit_line)
            result.commands.append(cmd_result)
            self._on_event("command_end", {
                "step_id": step.id, "command": cmd,
                "exit_code": cmd_result.exit_code, "duration_s": cmd_result.duration_s,
                "ok": cmd_result.ok,
            })
            if not cmd_result.ok:
                result.error = (
                    f"command failed: {cmd!r} "
                    f"(exit={cmd_result.exit_code}, timed_out={cmd_result.timed_out})"
                )
                break
        result.finished_at = time.monotonic()
        self._on_event("step_end", {
            "step_id": step.id, "ok": result.ok, "error": result.error,
            # Surface the fix-it text on failure (the no-agent robustness path:
            # Linux/OOD have no Tier-0 agent, so a clear remediation is the help).
            "remediation": step.remediation if not result.ok else "",
        })
        return result

    def run_all(self, *, only: Optional[Iterable[str]] = None) -> list[StepResult]:
        """Run every step in order (or only the ones in `only`).
        Stops on first failure."""
        only_set = set(only) if only is not None else None
        results: list[StepResult] = []
        for step in self.playbook.steps:
            if only_set is not None and step.id not in only_set:
                continue
            r = self.run_step(step)
            # Tier-0 repair: on failure, let the agent fix the system, then retry
            # the step (bounded). The re-run's exit code is the real verdict.
            attempt = 0
            while (not r.ok and self._on_step_failed is not None
                   and attempt < self._max_repair_attempts):
                attempt += 1
                if not self._on_step_failed(step, r, attempt):
                    break
                r = self.run_step(step)
            results.append(r)
            if not r.ok:
                break
        return results

    # ─── internals ─────────────────────────────────────────────────────────
    def _materialize_env(self) -> dict[str, str]:
        """Render the playbook's env_vars (which may reference $HOME, etc.) on top
        of the inherited environment. env_vars are DEFAULTS — an exported value
        (e.g. a custom ABA_HOME for a non-default install location, or a test
        sandbox) wins, so they don't clobber the caller's choice."""
        env = dict(self._base_env)
        for k, raw in self.playbook.env_vars.items():
            env.setdefault(k, os.path.expandvars(raw))
        return env

    def _run_one(self, cmd: str, *, env: dict[str, str], timeout: int,
                 on_line: Optional[Callable[[str], None]] = None) -> CommandResult:
        """Run one shell command, streaming output line-by-line to on_line as
        it arrives (so the UI shows live progress on long steps like the conda
        env build) while still accumulating the full output. Enforces the
        timeout even if the process blocks producing no output."""
        t0 = time.monotonic()
        proc = subprocess.Popen(
            cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env, cwd=self._cwd,
        )
        # Read in a thread so a deadline can be enforced even when readline
        # blocks (a hung command with no output).
        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def _reader() -> None:
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put(None)  # sentinel: stream closed

        threading.Thread(target=_reader, daemon=True).start()

        lines: list[str] = []
        timed_out = False
        deadline = t0 + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                item = q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if item is None:
                break
            lines.append(item)
            if on_line:
                on_line(item.rstrip("\n"))

        if timed_out:
            proc.kill()
        exit_code = proc.wait()
        return CommandResult(
            command=cmd, exit_code=(-1 if timed_out else exit_code),
            stdout="".join(lines), stderr="",
            duration_s=time.monotonic() - t0, timed_out=timed_out,
        )
