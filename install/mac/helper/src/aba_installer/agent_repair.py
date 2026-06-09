"""Agent-driven install repair (Tier-0, phase 1).

The deterministic playbook (install.yml) is the happy-path spine. When a step
fails, instead of dead-ending we hand the failure to Claude Code — run headless
(`claude -p`) with a scoped tool allowlist — to diagnose the *system* problem
(quarantined binary, missing tool, proxy/network, permissions, …), fix it, and
then we re-run the step. Off by default; enabled via ABA_INSTALL_AGENT_REPAIR.

Why Claude Code rather than a bespoke agent loop: it IS the agent harness
(shell + files + permissions + streaming), installs Node-free, and manages its
own auth. We bootstrap/locate the `claude` binary and drive it over a subprocess
boundary, keeping this module thin and fully unit-testable via an injectable
`runner` (no network/real CLI needed in tests)."""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

RECIPE_PATH = Path(__file__).with_name("repair_recipe.md")

# `dontAsk` permission mode denies anything not in this allowlist (the safety
# boundary for an unattended agent). Curated for common macOS install fixes;
# deliberately excludes `sudo`. Tune as we learn real failure modes.
DEFAULT_ALLOWED_TOOLS = [
    "Read", "Write",
    "Bash(ls *)", "Bash(cat *)", "Bash(echo *)", "Bash(mkdir *)", "Bash(chmod *)",
    "Bash(xattr *)",            # de-quarantine downloaded binaries (Gatekeeper)
    "Bash(curl *)",             # re-download / mirror
    "Bash(xcode-select *)",     # detect (cannot silently install) the CLT
    "Bash(git *)",
    "Bash(tar *)", "Bash(unzip *)",
    "Bash(file *)", "Bash(uname *)", "Bash(which *)", "Bash(networksetup *)",
]


@dataclass
class RepairOutcome:
    attempted: bool
    ok: bool                     # claude exited cleanly (≠ proof the step is fixed; the re-run is the judge)
    diagnosis: str = ""
    reason: str = ""             # why we couldn't attempt, if attempted is False
    raw: dict = field(default_factory=dict)


# ─── system probe ───────────────────────────────────────────────────────────
def _has_xcode_clt() -> bool:
    try:
        return subprocess.run(["xcode-select", "-p"],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def probe_system() -> dict:
    """Lightweight, fast facts to orient the repair agent."""
    def have(cmd: str) -> bool:
        return shutil.which(cmd) is not None
    return {
        "os": "macOS",
        "macos_version": platform.mac_ver()[0] or "unknown",
        "arch": platform.machine(),
        "shell": os.environ.get("SHELL", ""),
        "has_git": have("git"),
        "has_curl": have("curl"),
        "has_xcode_clt": _has_xcode_clt(),
        "aba_home": os.environ.get("ABA_HOME", ""),
    }


# ─── claude binary discovery ────────────────────────────────────────────────
def claude_path() -> Optional[str]:
    """Locate a usable `claude`: $ABA_HOME/bin, the native installer's
    ~/.local/bin, then PATH. Returns the path or None."""
    candidates = []
    home = os.environ.get("ABA_HOME")
    if home:
        candidates.append(Path(home) / "bin" / "claude")
    candidates.append(Path.home() / ".local" / "bin" / "claude")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return shutil.which("claude")


# Node-free native installer (installs ~/.local/bin/claude). DISABLE_AUTOUPDATER
# so it can't self-update mid-install.
CLAUDE_INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"


def _default_installer() -> None:
    subprocess.run(CLAUDE_INSTALL_CMD, shell=True, check=True, timeout=300,
                   env={**os.environ, "DISABLE_AUTOUPDATER": "1"})


def ensure_claude(*, installer: Optional[Callable] = None,
                  on_event: Optional[Callable[[str, dict], None]] = None) -> Optional[str]:
    """A usable `claude` path, installing the native CLI into the user's space if
    absent. `installer` is injectable for tests (default: the curl|bash native
    install). Returns None if it still isn't available after install."""
    p = claude_path()
    if p:
        return p
    emit = on_event or (lambda name, payload: None)
    emit("repair", {"phase": "bootstrap", "message": "Installing Claude Code (one-time)…"})
    try:
        (installer or _default_installer)()
    except Exception as e:  # noqa: BLE001
        emit("repair", {"phase": "error", "message": f"Claude Code install failed: {e}"})
        return None
    return claude_path()


# ─── invocation construction ────────────────────────────────────────────────
def build_repair_prompt(step_id: str, title: str, command: str,
                        error: str, probe: dict) -> str:
    return (
        f"An ABA install step failed on this Mac. Diagnose the SYSTEM cause, fix "
        f"it within the user's space, then verify.\n\n"
        f"Failed step: {step_id} — {title}\n"
        f"Command:\n{command}\n\n"
        f"Error / last output:\n{error}\n\n"
        f"System probe:\n{json.dumps(probe, indent=2)}\n\n"
        f"Apply the smallest fix that lets the command succeed (see your "
        f"instructions for common macOS failure modes). Do NOT re-run the whole "
        f"install. When done, briefly state what you changed."
    )


def build_claude_argv(claude: str, *, prompt: str,
                      recipe_path: Path = RECIPE_PATH,
                      allowed_tools: Optional[list[str]] = None,
                      add_dir: Optional[str] = None) -> list[str]:
    argv = [
        claude, "-p", prompt,
        "--append-system-prompt-file", str(recipe_path),
        "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(allowed_tools or DEFAULT_ALLOWED_TOOLS),
        "--output-format", "json",
    ]
    if add_dir:
        argv += ["--add-dir", add_dir]
    return argv


def _default_runner(argv: list[str], *, cwd: Optional[str], env: dict) -> dict:
    """Run `claude -p …` and parse its --output-format json result."""
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=600)
    out = (proc.stdout or "").strip()
    parsed = {}
    if out:
        try:
            parsed = json.loads(out)
        except Exception:  # noqa: BLE001
            parsed = {"result": out}
    parsed.setdefault("returncode", proc.returncode)
    if proc.returncode != 0 and not parsed.get("result"):
        parsed["result"] = (proc.stderr or "")[-400:]
    return parsed


# ─── orchestration ──────────────────────────────────────────────────────────
def run_repair(step_id: str, title: str, command: str, error: str, *,
               cwd: Optional[str] = None,
               claude: Optional[str] = None,
               runner: Optional[Callable] = None,
               recipe_path: Path = RECIPE_PATH,
               allowed_tools: Optional[list[str]] = None,
               on_event: Optional[Callable[[str, dict], None]] = None
               ) -> RepairOutcome:
    """Drive one repair round. `runner` is injectable for tests."""
    emit = on_event or (lambda name, payload: None)
    claude = claude or claude_path()
    if not claude:
        emit("repair", {"step_id": step_id, "phase": "skip",
                        "message": "Claude Code (claude) not available — cannot auto-repair."})
        return RepairOutcome(attempted=False, ok=False, reason="claude not available")

    probe = probe_system()
    prompt = build_repair_prompt(step_id, title, command, error, probe)
    argv = build_claude_argv(claude, prompt=prompt, recipe_path=recipe_path,
                             allowed_tools=allowed_tools, add_dir=cwd)
    emit("repair", {"step_id": step_id, "phase": "start",
                    "message": f"Asking Claude Code to diagnose “{title}”…"})

    env = dict(os.environ)
    env.setdefault("DISABLE_AUTOUPDATER", "1")   # don't let claude self-update mid-install
    run = runner or _default_runner
    try:
        parsed = run(argv, cwd=cwd, env=env)
    except Exception as e:  # noqa: BLE001
        emit("repair", {"step_id": step_id, "phase": "error", "message": str(e)})
        return RepairOutcome(attempted=True, ok=False, reason=f"runner failed: {e}")

    ok = (parsed.get("returncode", 0) == 0) and not parsed.get("is_error")
    diagnosis = (parsed.get("result") or "").strip()
    emit("repair", {"step_id": step_id, "phase": "done", "ok": ok,
                    "message": diagnosis[:400] or ("repaired" if ok else "could not repair")})
    return RepairOutcome(attempted=True, ok=ok, diagnosis=diagnosis, raw=parsed)


def make_repair_hook(*, cwd: Optional[str] = None,
                     runner: Optional[Callable] = None,
                     on_event: Optional[Callable[[str, dict], None]] = None,
                     enabled: bool = True,
                     ensure: bool = False) -> Callable:
    """Returns an Executor `on_step_failed(step, result, attempt) -> bool` hook.
    Runs a repair round and tells the executor to retry the step iff a repair was
    attempted (the step's actual re-run is the real verifier; the executor caps
    the number of attempts).

    `ensure=True` bootstraps the `claude` binary (ensure_claude) on the FIRST
    failure — so the happy path never pays for it, only a real failure does."""
    state = {"resolved": not ensure, "claude": None}

    def hook(step, result, attempt: int) -> bool:
        if not enabled:
            return False
        if not state["resolved"]:
            state["resolved"] = True
            state["claude"] = ensure_claude(on_event=on_event)
        err = (result.error or "")
        cmd = step.commands[-1] if getattr(step, "commands", None) else ""
        outcome = run_repair(step.id, step.title, cmd, err, cwd=cwd,
                             claude=state["claude"], runner=runner, on_event=on_event)
        return outcome.attempted
    return hook
