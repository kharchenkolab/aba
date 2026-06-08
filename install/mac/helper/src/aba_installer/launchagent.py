"""LaunchAgent — auto-start the helper on user login.

Renders templates/launchd.plist.template into
~/Library/LaunchAgents/com.kharchenkolab.aba.helper.plist and runs
launchctl load -w. User-level LaunchAgents need no admin.

Lifecycle:
  install_launch_agent()   — write plist + load it
  uninstall_launch_agent() — unload + remove plist
  is_loaded()              — check whether launchctl knows about it
"""
from __future__ import annotations
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aba_installer.paths import aba_home, installer_dir


LABEL = "com.kharchenkolab.aba.helper"


def template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / "launchd.plist.template"


def plist_destination() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


@dataclass
class AgentContext:
    venv_python: Path     # path to the helper's venv python3
    helper_dir: Path      # working directory + log destination
    aba_home: Path

    def substitutions(self) -> dict[str, str]:
        return {
            "VENV_PYTHON": str(self.venv_python),
            "HELPER_DIR":  str(self.helper_dir),
            "ABA_HOME":    str(self.aba_home),
        }


def default_context() -> AgentContext:
    home = aba_home()
    helper = installer_dir()
    # The venv is created by setup.command at $HELPER_DIR/venv; if that's
    # missing, fall back to the current python (useful for tests).
    venv_py = helper / "venv" / "bin" / "python"
    if not venv_py.exists():
        venv_py = Path(sys.executable)
    return AgentContext(venv_python=venv_py, helper_dir=helper, aba_home=home)


def render(ctx: AgentContext, *, template_text: Optional[str] = None) -> str:
    text = template_text if template_text is not None else template_path().read_text()
    for k, v in ctx.substitutions().items():
        text = text.replace(f"@@{k}@@", v)
    return text


# ─── launchctl wrappers ────────────────────────────────────────────────────
def _launchctl(*args: str) -> tuple[int, str, str]:
    proc = subprocess.run(["launchctl", *args], capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def is_loaded() -> bool:
    """True if launchctl currently has the agent loaded. Cheap; works on any
    macOS version."""
    rc, out, _ = _launchctl("list")
    if rc != 0:
        return False
    for line in out.splitlines():
        if line.endswith(f"\t{LABEL}") or line.endswith(LABEL):
            return True
    return False


def install_launch_agent(ctx: Optional[AgentContext] = None) -> Path:
    """Write the plist + tell launchctl to load it. Idempotent — re-running
    rewrites the plist (e.g. after a helper update changed paths) and
    reloads."""
    ctx = ctx or default_context()
    dest = plist_destination()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(ctx))
    # Unload any prior instance, then load fresh
    if is_loaded():
        _launchctl("unload", str(dest))
    _launchctl("load", "-w", str(dest))
    return dest


def uninstall_launch_agent() -> bool:
    """Unload + delete the plist. Returns True if anything was removed."""
    dest = plist_destination()
    removed = False
    if is_loaded():
        _launchctl("unload", str(dest))
    if dest.exists():
        dest.unlink()
        removed = True
    return removed
