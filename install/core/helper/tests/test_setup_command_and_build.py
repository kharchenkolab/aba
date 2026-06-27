"""H6 — setup.command + build pipeline.

Validates the user-facing .command file shape, and (when `make` is
available) that the build pipeline produces the expected artifacts.
"""
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest


# test file lives at: install/core/helper/tests/<this>.py — parents[4] = repo root.
REPO_ROOT = Path(__file__).resolve().parents[4]
SETUP_CMD = REPO_ROOT / "install/mac/setup.command"
BUILD_DIR = REPO_ROOT / "install/mac/build"


def test_setup_command_exists_and_has_shebang():
    assert SETUP_CMD.exists(), f"{SETUP_CMD} missing"
    body = SETUP_CMD.read_text()
    assert body.startswith("#!/usr/bin/env bash"), "setup.command must be a bash script"


def test_setup_command_targets_aba_home_space_free():
    # The install root must be space-free — the conda r-base wrapper breaks
    # on a space in its prefix path. See paths.aba_home().
    body = SETUP_CMD.read_text()
    assert 'ABA_HOME="$HOME/.aba"' in body


def test_setup_command_clones_repo_and_installs_helper_from_it():
    # Repo-clone rollout: setup.command clones the repo (URL overridable for
    # SSH/private) and installs the helper from install/core/helper inside it —
    # no separate helper tarball / release needed.
    body = SETUP_CMD.read_text()
    assert "git clone" in body
    assert "kharchenkolab/aba" in body
    assert 'ABA_REPO_URL' in body and 'ABA_RECIPES_URL' in body  # overridable
    assert "install/core/helper" in body


def test_setup_command_installs_launchagent_via_helper():
    # The plist is a template needing path substitution, so setup.command
    # renders + loads it through the helper's own install_launch_agent()
    # rather than copying it by hand (which shipped unrendered @@…@@ markers).
    body = SETUP_CMD.read_text()
    assert "install_launch_agent" in body


def test_setup_command_opens_browser_after_helper_ready():
    body = SETUP_CMD.read_text()
    # Loops on /ready before opening
    assert "/ready" in body
    assert "open " in body  # macOS `open <url>`


def test_setup_command_refuses_non_macos():
    body = SETUP_CMD.read_text()
    assert 'uname -s' in body and 'Darwin' in body


# ─── build pipeline ────────────────────────────────────────────────────────
@pytest.mark.skipif(shutil.which("make") is None or shutil.which("zip") is None,
                    reason="make / zip not available")
def test_build_produces_setup_zip(tmp_path):
    """Run `make all` against the real Makefile, then validate the output.

    Uses the real build/Makefile but redirects OUT_DIR into the test
    tempdir so the repo stays clean. The rollout is a single artifact —
    ABA-Setup.zip — since the .command clones the repo + installs the helper
    from it (no separate helper tarball).
    """
    out_dir = tmp_path / "out"
    result = subprocess.run(
        ["make", "-C", str(BUILD_DIR), "all",
         f"OUT_DIR={out_dir}",
         f"SETUP_ZIP={out_dir}/ABA-Setup.zip"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (
        f"make failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    zp = out_dir / "ABA-Setup.zip"
    assert zp.exists(), f"setup zip missing: {result.stdout}"

    # zip contains ABA Setup.command with the execute bit set
    with zipfile.ZipFile(zp) as zf:
        names = zf.namelist()
        assert "ABA Setup.command" in names
        info = zf.getinfo("ABA Setup.command")
        # ZipFile encodes Unix perms in upper 16 bits of external_attr
        perms = (info.external_attr >> 16) & 0o777
        assert perms & 0o100, f"execute bit missing on archived .command (perms={oct(perms)})"
