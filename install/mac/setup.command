#!/usr/bin/env bash
# ABA Setup — bootstrap installer for macOS.
#
# Distributed as ABA-Setup.zip (this single file). Double-clicking it:
#   1. clones the ABA repo + recipe library,
#   2. installs the lightweight helper service from the repo,
#   3. starts the helper on localhost and opens the browser.
# Everything heavier (the conda env, the frontend build) happens in the
# browser UI after you click "Install ABA".
#
# Why clone here instead of downloading a helper bundle: the clones run in
# YOUR shell, so your SSH agent / git credentials work — which means you can
# install while the repos are still private. The helper itself then only
# talks to public conda channels, never GitHub. When the repos go public,
# the defaults below just work with no overrides.
#
# Override the sources (e.g. SSH while private, or a fork):
#   ABA_REPO_URL=git@github.com:kharchenkolab/aba.git \
#   ABA_RECIPES_URL=git@github.com:kharchenkolab/aba-recipe-pack.git \
#       "./ABA Setup.command"
#
# Override the branch (default: whatever each remote's HEAD points to). Useful
# for testing a feature branch end-to-end before merging to main:
#   ABA_REPO_BRANCH=your-feature-branch "./ABA Setup.command"
# Only affects the initial clone — on subsequent runs this script `git pull`s
# whatever branch the local checkout is on, so to switch branches after the
# fact, remove $REPO_DIR/aba and re-run.
#
# What this touches:
#   ~/.aba/repo/{aba,aba-recipe-pack}   (source)
#   ~/.aba/installer/               (helper venv + state)
#   ~/Library/LaunchAgents/com.kharchenkolab.aba.helper.plist  (auto-start)

set -euo pipefail

ABA_HOME="$HOME/.aba"
REPO_DIR="$ABA_HOME/repo"
HELPER_DIR="$ABA_HOME/installer"
ABA_REPO_URL="${ABA_REPO_URL:-https://github.com/kharchenkolab/aba}"
ABA_RECIPES_URL="${ABA_RECIPES_URL:-https://github.com/kharchenkolab/aba-recipe-pack}"

cat <<EOF
ABA Setup
─────────
Setting up ABA under:
    $ABA_HOME

EOF

# Pre-flight
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: ABA Setup runs on macOS. Detected: $(uname -s)" >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "Error: git not found. Install the Xcode Command Line Tools first:" >&2
  echo "    xcode-select --install" >&2
  exit 1
fi
PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "Error: macOS 12.3+ ships python3; could not find it on this system." >&2
  exit 1
fi

mkdir -p "$REPO_DIR" "$HELPER_DIR"

# Clone (or refresh) the repo + recipe library — in this shell, so SSH keys /
# git credentials work while the repos are private.
clone_or_pull() {  # $1=url  $2=dest  $3=branch (optional)
  if [[ -d "$2/.git" ]]; then
    echo "Updating $(basename "$2") …"
    git -C "$2" pull --ff-only || true
  else
    echo "Cloning $(basename "$2") …"
    if [[ -n "${3:-}" ]]; then
      git clone -b "$3" --depth 1 "$1" "$2"
    else
      git clone --depth 1 "$1" "$2"
    fi
  fi
}
clone_or_pull "$ABA_REPO_URL"    "$REPO_DIR/aba"          "${ABA_REPO_BRANCH:-}"
clone_or_pull "$ABA_RECIPES_URL" "$REPO_DIR/aba-recipe-pack"  "${ABA_RECIPES_BRANCH:-}"

# Install the helper from the cloned repo into a private venv (never touches
# the user's system Python).
if [[ ! -x "$HELPER_DIR/venv/bin/python" ]]; then
  echo "Creating helper venv …"
  "$PY" -m venv "$HELPER_DIR/venv"
fi
"$HELPER_DIR/venv/bin/pip" install --quiet --upgrade pip
"$HELPER_DIR/venv/bin/pip" install --quiet "$REPO_DIR/aba/install/mac/helper"

# Render + load the LaunchAgent so the helper auto-starts on login — and
# starts it now (RunAtLoad). The plist is a template needing path
# substitution, so we render it through the helper's own code. If that fails
# on this Mac, fall back to starting the helper directly.
export ABA_HOME
if ! "$HELPER_DIR/venv/bin/python" -c \
     "from aba_installer.launchagent import install_launch_agent; install_launch_agent()"; then
  echo "Warning: could not install the auto-start LaunchAgent; starting the helper directly for this session." >&2
  nohup "$HELPER_DIR/venv/bin/python" -m aba_installer.service \
    >> "$HELPER_DIR/helper.out.log" 2>&1 &
fi

# Tier-0-tray (misc/mac-install.md § 3c): install ABA.app into
# ~/Applications + register the tray LaunchAgent. v1 rollout is OPT-IN
# via ABA_INSTALL_TRAY=1 — flip the default to on after the early users
# have validated it. Failures are non-fatal; the rest of the install
# still completes and the browser UI still works.
if [[ "${ABA_INSTALL_TRAY:-}" == "1" ]]; then
  echo "Installing ABA Tray …"
  "$HELPER_DIR/venv/bin/python" -m aba_installer.tray_install || \
    echo "Warning: ABA Tray install failed; continuing without the menu-bar app." >&2
fi

# Wait for the helper to come up, then open the browser.
echo "Starting helper …"
PORT=8765
for _ in $(seq 1 60); do
  if [[ -f "$HELPER_DIR/port.txt" ]]; then
    PORT="$(cat "$HELPER_DIR/port.txt")"
  fi
  if curl -fs "http://127.0.0.1:$PORT/ready" > /dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

# Use localhost (not 127.0.0.1) so the UI tab shares an origin with the OAuth
# callback (http://localhost:PORT/callback) — lets the callback advance the
# Setup tab after Sign in with Claude.ai.
URL="http://localhost:$PORT/"
echo
echo "ABA Setup is running."
echo "Opening $URL in your browser…"
open "$URL" || true

# Best-effort: close this Terminal window so the user isn't left at a blank
# prompt. Quietly ignored if the user's Terminal isn't the default.
osascript <<APPLESCRIPT 2>/dev/null || true
tell application "Terminal"
  set windowList to (every window whose name contains "ABA Setup" or name contains "setup.command")
  repeat with w in windowList
    close w saving no
  end repeat
end tell
APPLESCRIPT

exit 0
