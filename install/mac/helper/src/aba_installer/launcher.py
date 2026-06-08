"""aba launcher renderer + installer.

Reads templates/aba.template, substitutes ABA_HOME + env paths, writes
to ~/bin/aba (or /usr/local/bin/aba with --global) with mode 0755.

Substitution uses `@@KEY@@` markers rather than f-strings so the
template stays a valid shell script (no Python-only syntax). Bash
variables in the template that should stay bash variables (e.g.
`$ABA_PORT`) are left untouched.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from aba_installer.paths import aba_home, env_dir, repo_dir, installer_dir


DEFAULT_PORT = 8000


def template_path() -> Path:
    return Path(__file__).resolve().parent / "templates" / "aba.template"


@dataclass
class LauncherContext:
    aba_home: Path
    aba_runtime_dir: Path
    aba_env: Path
    aba_repo: Path
    aba_port: int = DEFAULT_PORT

    def substitutions(self) -> dict[str, str]:
        return {
            "ABA_HOME":        str(self.aba_home),
            "ABA_RUNTIME_DIR": str(self.aba_runtime_dir),
            "ABA_ENV":         str(self.aba_env),
            "ABA_REPO":        str(self.aba_repo),
            "ABA_PORT":        str(self.aba_port),
        }


def default_context(port: int = DEFAULT_PORT) -> LauncherContext:
    home = aba_home()
    return LauncherContext(
        aba_home=home,
        aba_runtime_dir=home / "runtime",
        aba_env=env_dir(),
        aba_repo=repo_dir(),
        aba_port=port,
    )


def render(ctx: LauncherContext, *, template_text: Optional[str] = None) -> str:
    """Render the launcher script with the given context."""
    text = template_text if template_text is not None else template_path().read_text()
    for k, v in ctx.substitutions().items():
        text = text.replace(f"@@{k}@@", v)
    return text


# ─── install destinations ──────────────────────────────────────────────────
def user_install_path() -> Path:
    return Path.home() / "bin" / "aba"


def global_install_path() -> Path:
    return Path("/usr/local/bin/aba")


def install_to_user_bin(ctx: Optional[LauncherContext] = None) -> Path:
    """Write the launcher to ~/bin/aba. Creates ~/bin if needed. No admin."""
    ctx = ctx or default_context()
    dest = user_install_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(ctx))
    os.chmod(dest, 0o755)
    return dest


def install_to_global(ctx: Optional[LauncherContext] = None) -> Path:
    """Write the launcher to /usr/local/bin/aba. Requires admin (caller is
    responsible for that — typically by running this under sudo)."""
    ctx = ctx or default_context()
    dest = global_install_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(ctx))
    os.chmod(dest, 0o755)
    return dest


def discover_installed() -> Optional[Path]:
    """Find the active launcher — prefers ~/bin/aba, falls back to global."""
    for p in (user_install_path(), global_install_path()):
        if p.exists() and p.is_file():
            return p
    return None
