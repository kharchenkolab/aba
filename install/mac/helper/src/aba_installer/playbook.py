"""Playbook parser + step executor.

A playbook is a YAML document describing the deterministic install
sequence. The executor runs steps in order, capturing stdout/stderr and
exit codes; results stream back to the API caller via an
event-callback so the UI can render progress in real time.

Step shape (see playbook.yml):
  - id: <stable-string-id>
    title: <human title for the UI>
    why:   <one-paragraph rationale, shown on hover or in "show details">
    commands: [<shell command>, ...]
    timeout_seconds: <int, default 300>
"""
from __future__ import annotations
import os
import shlex
import subprocess
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
                 cwd: Optional[Path] = None):
        self.playbook = playbook
        self._on_event = on_event or (lambda name, payload: None)
        self._base_env = base_env if base_env is not None else dict(os.environ)
        self._cwd = str(cwd) if cwd is not None else None

    # ─── public API ────────────────────────────────────────────────────────
    def run_step(self, step: Step) -> StepResult:
        started = time.monotonic()
        self._on_event("step_start", {"step_id": step.id, "title": step.title})
        env = self._materialize_env()
        result = StepResult(step_id=step.id, started_at=started, finished_at=started)
        for cmd in step.commands:
            self._on_event("command_start", {"step_id": step.id, "command": cmd})
            cmd_result = self._run_one(cmd, env=env, timeout=step.timeout_seconds)
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
            results.append(r)
            if not r.ok:
                break
        return results

    # ─── internals ─────────────────────────────────────────────────────────
    def _materialize_env(self) -> dict[str, str]:
        """Render the playbook's env_vars (which may reference $HOME, etc.)
        on top of the inherited environment."""
        env = dict(self._base_env)
        for k, raw in self.playbook.env_vars.items():
            env[k] = os.path.expandvars(raw)
        return env

    def _run_one(self, cmd: str, *, env: dict[str, str], timeout: int) -> CommandResult:
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                env=env, cwd=self._cwd, timeout=timeout,
            )
            return CommandResult(
                command=cmd, exit_code=proc.returncode,
                stdout=proc.stdout, stderr=proc.stderr,
                duration_s=time.monotonic() - t0,
            )
        except subprocess.TimeoutExpired as e:
            return CommandResult(
                command=cmd, exit_code=-1,
                stdout=(e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, (bytes, bytearray)) else (e.stdout or ""),
                stderr=(e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, (bytes, bytearray)) else (e.stderr or ""),
                duration_s=time.monotonic() - t0,
                timed_out=True,
            )
