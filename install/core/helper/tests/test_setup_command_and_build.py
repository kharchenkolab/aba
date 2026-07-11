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
SETUP_SH = REPO_ROOT / "install/linux/setup.sh"
INSTALL_YML = REPO_ROOT / "install/core/helper/src/aba_installer/install.yml"
BUILD_DIR = REPO_ROOT / "install/mac/build"


# ── ABA_ENV_PREWARM (staged vs eager) — the lazy-env-init knob ──────────────
def test_install_yml_documents_prewarm_knob():
    """The canonical env-var doc block must document ABA_ENV_PREWARM with the
    eager default + the staged option (misc/lazy_env_init.md)."""
    body = INSTALL_YML.read_text()
    assert "ABA_ENV_PREWARM" in body
    assert "eager" in body and "staged" in body


def test_mac_defaults_prewarm_staged():
    """A personal Mac defaults to staged and persists it to config.env, while
    honoring an explicit override (the `${ABA_ENV_PREWARM:-staged}` form)."""
    body = SETUP_CMD.read_text()
    assert 'ABA_ENV_PREWARM="${ABA_ENV_PREWARM:-staged}"' in body
    assert "config.env" in body and "ABA_ENV_PREWARM=" in body


def test_linux_prewarm_per_profile():
    """linux setup.sh: cluster-personal (Slurm/shared) → eager; local → staged;
    an explicit ABA_ENV_PREWARM wins; persisted via write_cfg + exported."""
    body = SETUP_SH.read_text()
    assert 'PREWARM="eager"' in body and 'PREWARM="staged"' in body
    assert "cluster-personal" in body
    assert "write_cfg ABA_ENV_PREWARM" in body and "export ABA_ENV_PREWARM" in body
    # explicit override respected
    assert 'PREWARM="$ABA_ENV_PREWARM"' in body


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


# ─── ref pin (consolidated onto ABA_REF / RECIPES_REF) ──────────────────────
def test_setup_command_ref_pin_consolidated():
    # One pin knob: ABA_REF / RECIPES_REF (branch/tag/commit), with the older
    # ABA_REPO_BRANCH / ABA_RECIPES_BRANCH kept only as back-compat aliases.
    # The clone calls must pass the resolved $ABA_REF / $RECIPES_REF — NOT the
    # legacy branch vars directly (that would drop tag/commit support).
    body = SETUP_CMD.read_text()
    assert 'ABA_REF="${ABA_REF:-${ABA_REPO_BRANCH:-}}"' in body
    assert 'RECIPES_REF="${RECIPES_REF:-${ABA_RECIPES_BRANCH:-}}"' in body
    assert 'clone_or_pull "$ABA_REPO_URL"    "$REPO_DIR/aba"             "$ABA_REF"' in body
    assert 'clone_or_pull "$ABA_RECIPES_URL" "$REPO_DIR/aba-recipe-pack" "$RECIPES_REF"' in body


def _extract_bash_func(body: str, name: str) -> str:
    """Pull a top-level `name() { … }` function (closing brace at col 0) out of a script."""
    lines = body.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith(f"{name}() {{"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return "\n".join(lines[start:end + 1])


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
@pytest.mark.parametrize("ref_env,expect", [
    ({}, "B"),                                  # neither → default branch
    ({"ABA_REPO_BRANCH": "v1"}, "A"),           # legacy alias resolves
    ({"ABA_REF": "v1"}, "A"),                   # tag
    ({"ABA_REF": "__SHA__"}, "A"),              # bare commit SHA (full-clone fallback)
    ({"ABA_REF": "v1", "ABA_REPO_BRANCH": "main"}, "A"),  # ABA_REF wins over alias
])
def test_setup_command_clone_honors_ref(tmp_path, ref_env, expect):
    """The real clone_or_pull from setup.command, driven by the same
    ABA_REF/alias resolution, honors a branch / tag / commit against a throwaway
    repo (commit A tagged v1, then commit B on main)."""
    def git(*a, cwd):
        subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)
    src = tmp_path / "src"; src.mkdir()
    git("init", "-q", "-b", "main", ".", cwd=src)
    git("config", "user.email", "t@t", cwd=src); git("config", "user.name", "t", cwd=src)
    (src / "f").write_text("A"); git("add", "-A", cwd=src); git("commit", "-qm", "A", cwd=src)
    sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src,
                           capture_output=True, text=True).stdout.strip()
    git("tag", "v1", cwd=src)
    (src / "f").write_text("B"); git("commit", "-qam", "B", cwd=src)

    env = {k: (sha_a if v == "__SHA__" else v) for k, v in ref_env.items()}
    fn = _extract_bash_func(SETUP_CMD.read_text(), "clone_or_pull")
    # Mirror setup.command's alias resolution + call, then report HEAD's subject.
    script = f'''
set -euo pipefail
fail() {{ echo "FAIL: $*" >&2; exit 1; }}
{fn}
ABA_REF="${{ABA_REF:-${{ABA_REPO_BRANCH:-}}}}"
clone_or_pull "{src}" "{tmp_path}/out" "$ABA_REF"
git -C "{tmp_path}/out" log -1 --format=%s
'''
    out = subprocess.run(["bash", "-c", script], env={**os.environ, **env},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == expect  # last line = HEAD subject


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
