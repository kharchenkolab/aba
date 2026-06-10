"""Tier-0-tray installer step.

setup.command shells into ``python -m aba_installer.tray_install`` after the
helper venv is up. This module:

1. Copies the bundle template (``install/mac/tray/ABA.app``) into
   ``~/Applications/ABA.app``, replacing any prior copy.
2. Clears the ``com.apple.quarantine`` xattr (Gatekeeper) so the user
   doesn't get a "downloaded from Internet" dialog on first launch — same
   trust-gradient logic the agent-repair recipe uses for micromamba (the
   user has already consented to setup.command's parent quarantine).
3. Installs the tray LaunchAgent so ABA.app auto-starts on next login.

Gated by ``ABA_INSTALL_TRAY=1`` during the v1 rollout. Once stable, the
default flips and the env var becomes opt-out."""
from __future__ import annotations
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional


# Where the bundle template ships inside the repo. setup.command runs this
# module from the helper's installed venv; the repo is at $ABA_REPO_DIR/aba
# (or the standard location ``$ABA_HOME/repo/aba``).
def _bundle_template() -> Path:
    # First try ABA_HOME/repo/aba (production); fall back to walking up
    # from this file (editable install / tests).
    env_home = os.environ.get("ABA_HOME")
    if env_home:
        cand = Path(env_home) / "repo" / "aba" / "install" / "mac" / "tray" / "ABA.app"
        if cand.exists():
            return cand
    # Editable install: this file is in
    # install/mac/helper/src/aba_installer/tray_install.py
    # → walk up 5 to the repo root, then drill into install/mac/tray/ABA.app
    here = Path(__file__).resolve()
    for parents in (here.parents[5] if len(here.parents) >= 6 else None,
                    here.parents[6] if len(here.parents) >= 7 else None):
        if parents is None:
            continue
        cand = parents / "install" / "mac" / "tray" / "ABA.app"
        if cand.exists():
            return cand
    raise FileNotFoundError(
        "Could not locate the ABA.app bundle template — expected at "
        "$ABA_HOME/repo/aba/install/mac/tray/ABA.app")


def _user_applications() -> Path:
    return Path.home() / "Applications"


def _run(argv: list[str], **kwargs):
    """subprocess.run wrapper kept module-level so tests can monkeypatch."""
    return subprocess.run(argv, capture_output=True, text=True, **kwargs)


def _clear_quarantine(path: Path) -> None:
    """Best-effort xattr -dr. Failure non-fatal — user sees one Gatekeeper
    dialog instead of a broken install."""
    proc = _run(["xattr", "-dr", "com.apple.quarantine", str(path)])
    if proc.returncode != 0:
        # Doesn't print to stdout in production; tests can capture via capsys.
        print(f"warn: xattr clear failed ({proc.returncode}): "
              f"{(proc.stderr or '').strip()}", file=sys.stderr)


def _copy_tree_preserving_modes(src: Path, dest: Path) -> None:
    """Replace dest entirely with a fresh copy of src. Preserves the +x bit
    on Contents/MacOS/aba-tray (shutil.copytree honors stat by default with
    copy2)."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, copy_function=shutil.copy2)
    # Extra belt: even with copy2, some filesystems normalize mode bits.
    # Force +x on the launcher.
    launcher = dest / "Contents" / "MacOS" / "aba-tray"
    if launcher.exists():
        st = launcher.stat()
        launcher.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_launch_agent() -> None:
    """Module-level indirection so tests can replace without importing
    tray_launchagent at test-collection time (it touches launchctl when
    is_loaded() is called against the real system)."""
    from aba_installer import tray_launchagent
    tray_launchagent.install_launch_agent()


# ─── public entrypoint ─────────────────────────────────────────────────
def install_tray() -> Path:
    """Run the full install: copy the bundle, clear quarantine, install
    the LaunchAgent. Returns the destination .app path."""
    src = _bundle_template()
    apps = _user_applications()
    apps.mkdir(parents=True, exist_ok=True)
    dest = apps / "ABA.app"
    _copy_tree_preserving_modes(src, dest)
    _clear_quarantine(dest)
    _install_launch_agent()
    return dest


def main() -> int:
    """``setup.command`` shells here. No-op unless ``ABA_INSTALL_TRAY=1``."""
    flag = (os.environ.get("ABA_INSTALL_TRAY") or "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return 0
    try:
        dest = install_tray()
    except FileNotFoundError as e:
        print(f"tray install skipped: {e}", file=sys.stderr)
        return 0
    print(f"ABA Tray installed → {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
