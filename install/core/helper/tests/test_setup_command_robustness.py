"""Executable robustness tests for the setup.command BOOTSTRAP regime.

Unlike test_setup_command_and_build.py (which greps the script body), these
actually RUN setup.command under stubbed external tools (git / python3 / curl /
open / osascript) and assert on behaviour for the failure paths — the bootstrap
runs before the helper/web-UI/agent exist, so the terminal text + exit code are
the ONLY feedback a user gets.

Stubs are tiny scripts on a throwaway PATH; scenarios are selected by env vars
the stubs read (STUB_GIT_CLONE_FAIL, STUB_PIP_FAIL, STUB_NEVER_READY, …).
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SETUP_CMD = REPO_ROOT / "install/mac/setup.command"

# A unified stub for the python3 family. `-m venv DIR` lays down venv/bin/{python,pip}
# as copies of this same stub; each then dispatches on argv + env flags.
PY_STUB = r"""#!/usr/bin/env bash
# Dispatches as system-python3, venv/bin/python, or venv/bin/pip.
self="$(basename "$0")"
case "$1" in
  -m)
    case "$2" in
      venv)
        d="$3"; mkdir -p "$d/bin"
        cp "$STUB_SELF" "$d/bin/python"; cp "$STUB_SELF" "$d/bin/pip"
        chmod +x "$d/bin/python" "$d/bin/pip"; exit 0 ;;
      pip) exit 0 ;;                       # python -m pip ... (upgrade pip)
      aba_installer.tray_install) exit "${STUB_TRAY_FAIL:-0}" ;;
      aba_installer.service) exit 0 ;;
      *) exit 0 ;;
    esac ;;
  install)                                  # invoked as `pip install …`
    if [ -n "${STUB_PIP_FAIL:-}" ]; then
      echo "ERROR: Could not build wheels for foo" >&2; exit 1
    fi
    exit 0 ;;
  -c)                                       # install_launch_agent() etc.
    exit "${STUB_LAUNCHAGENT_FAIL:-0}" ;;
  --version) echo "Python 3.12.0 (stub)"; exit 0 ;;
  *) exit 0 ;;
esac
"""

GIT_STUB = r"""#!/usr/bin/env bash
# Subcommand is the first non -C/-flag token.
sub=""; for a in "$@"; do case "$a" in -C) ;; -*) ;; */*|/*) ;; *) sub="$a"; break;; esac; done
case " $* " in
  *" clone "*)
    if [ -n "${STUB_GIT_CLONE_FAIL:-}" ]; then
      echo "git@github.com: Permission denied (publickey)." >&2
      echo "fatal: Could not read from remote repository." >&2
      exit 128
    fi
    dest="${@: -1}"; mkdir -p "$dest/.git"; exit 0 ;;
  *" pull "*)
    [ -n "${STUB_GIT_PULL_FAIL:-}" ] && { echo "fatal: Not possible to fast-forward" >&2; exit 1; }
    exit 0 ;;
  *) exit 0 ;;
esac
"""

CURL_STUB = r"""#!/usr/bin/env bash
case " $* " in
  *"/ready"*) [ -n "${STUB_NEVER_READY:-}" ] && exit 7 || exit 0 ;;
  *) exit 0 ;;
esac
"""

UNAME_STUB = '#!/usr/bin/env bash\necho Darwin\n'
# open / osascript record that they were called, so we can detect a browser open
# of a (possibly dead) page and a terminal auto-close attempt.
OPEN_STUB = '#!/usr/bin/env bash\ntouch "$STUB_MARKER_DIR/open_called"\nexit 0\n'
OSA_STUB = '#!/usr/bin/env bash\ntouch "$STUB_MARKER_DIR/osascript_called"\nexit 0\n'


@pytest.fixture
def harness(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    binr = tmp_path / "bin"; binr.mkdir()
    markers = tmp_path / "markers"; markers.mkdir()

    def w(name, body):
        p = binr / name; p.write_text(body); p.chmod(0o755); return p

    py = w("python3", PY_STUB)
    w("git", GIT_STUB); w("curl", CURL_STUB); w("uname", UNAME_STUB)
    w("open", OPEN_STUB); w("osascript", OSA_STUB)

    def run(**flags):
        env = {
            "HOME": str(home),
            # keep coreutils (mkdir, cat, sleep, basename, seq, chmod, cp) available
            "PATH": f"{binr}:/usr/bin:/bin",
            "STUB_SELF": str(py),
            "STUB_MARKER_DIR": str(markers),
            "ABA_INSTALL_TRAY": "0",          # skip tray in bootstrap tests
        }
        env.update({k: v for k, v in flags.items()})
        return subprocess.run(["bash", str(SETUP_CMD)], env=env,
                              capture_output=True, text=True, timeout=120)

    run.home = home
    run.markers = markers
    return run


def _out(r):
    return (r.stdout or "") + (r.stderr or "")


# ─── happy path ─────────────────────────────────────────────────────────────
def test_happy_path_opens_browser_and_closes_terminal(harness):
    r = harness()
    assert r.returncode == 0, _out(r)
    assert (harness.markers / "open_called").exists(), "should open the browser"
    assert (harness.markers / "osascript_called").exists(), "should auto-close the terminal on success"


# ─── clone failure → friendly, actionable message ────────────────────────────
def test_clone_failure_is_actionable_and_aborts(harness):
    r = harness(STUB_GIT_CLONE_FAIL="1")
    assert r.returncode != 0, "clone failure must abort"
    out = _out(r).lower()
    # Should name the real fix path for the common private-repo/SSH case, not
    # leave the user staring at a raw 'Permission denied (publickey)'.
    assert "ssh" in out or "aba_repo_url" in out or "access" in out, _out(r)
    assert not (harness.markers / "open_called").exists(), "must not open a browser after a clone failure"


# ─── pip failure → visible, aborts ───────────────────────────────────────────
def test_pip_failure_aborts_and_is_not_silent(harness):
    r = harness(STUB_PIP_FAIL="1")
    assert r.returncode != 0, "pip failure must abort"
    assert not (harness.markers / "open_called").exists()


# ─── helper never becomes ready → no false success, no dead page, no auto-close ─
def test_helper_never_ready_does_not_falsely_succeed(harness):
    r = harness(STUB_NEVER_READY="1")
    out = _out(r)
    assert r.returncode != 0, "a helper that never readies must exit non-zero"
    assert "ABA Setup is running" not in out, "must not claim success when the helper never came up"
    assert "log" in out.lower(), "should point the user at the helper log"
    assert not (harness.markers / "open_called").exists(), "must not open a dead browser page"
    assert not (harness.markers / "osascript_called").exists(), "must NOT auto-close the terminal on failure"


# ─── ERR trap → branded failure footer on any abort ──────────────────────────
def test_failure_prints_branded_footer(harness):
    r = harness(STUB_PIP_FAIL="1")
    assert "ABA Setup failed" in _out(r), "an abort should print a branded, actionable footer"
