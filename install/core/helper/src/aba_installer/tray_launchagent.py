"""Tier-0-tray LaunchAgent — auto-start ABA.app at login.

Sibling to ``launchagent.py`` (the helper service plist); both live in
``~/Library/LaunchAgents/`` under distinct labels so the user can
enable/disable each independently via System Settings → General → Login
Items.

Lifecycle:
  install_launch_agent()   — write plist + load it
  uninstall_launch_agent() — unload + remove plist
  is_loaded()              — check whether launchctl knows about it
"""
from __future__ import annotations
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aba_installer.paths import aba_home, installer_dir


LABEL = "com.kharchenkolab.aba.tray"


def template_path() -> Path:
    return (Path(__file__).resolve().parent / "templates" /
            "launchd-tray.plist.template")


def plist_destination() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


@dataclass
class AgentContext:
    """Substitutions for the tray plist template."""
    app_launcher: Path     # …/ABA.app/Contents/MacOS/aba-tray
    helper_dir: Path       # log destination
    aba_home: Path

    def substitutions(self) -> dict[str, str]:
        return {
            "APP_LAUNCHER": str(self.app_launcher),
            "HELPER_DIR":   str(self.helper_dir),
            "ABA_HOME":     str(self.aba_home),
        }


def _user_applications() -> Path:
    """User-local Applications folder. macOS treats this the same as
    /Applications/ for Spotlight/Finder/Launchpad — and we don't need
    admin to drop a bundle there."""
    return Path.home() / "Applications"


def default_context() -> AgentContext:
    return AgentContext(
        app_launcher=(_user_applications() / "ABA.app" /
                      "Contents" / "MacOS" / "aba-tray"),
        helper_dir=installer_dir(),
        aba_home=aba_home(),
    )


def render(ctx: AgentContext, *, template_text: Optional[str] = None) -> str:
    text = template_text if template_text is not None else template_path().read_text()
    for k, v in ctx.substitutions().items():
        text = text.replace(f"@@{k}@@", v)
    return text


# ─── launchctl wrappers — same shape as launchagent.py ─────────────────
def _launchctl(*args: str) -> tuple[int, str, str]:
    proc = subprocess.run(["launchctl", *args], capture_output=True,
                          text=True, check=False)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def is_loaded() -> bool:
    rc, out, _ = _launchctl("list")
    if rc != 0:
        return False
    for line in out.splitlines():
        if line.endswith(f"\t{LABEL}") or line.endswith(LABEL):
            return True
    return False


def install_launch_agent(ctx: Optional[AgentContext] = None) -> Path:
    """Write the plist + launchctl-load it. Idempotent."""
    ctx = ctx or default_context()
    dest = plist_destination()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(ctx))
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
