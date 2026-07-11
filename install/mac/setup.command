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
# Pin what to clone with ABA_REF / RECIPES_REF (a branch, tag, or commit;
# default: each remote's HEAD). Useful for testing a feature branch or a pinned
# release end-to-end. This is the same pin the installer/update playbook honors —
# canonical doc: install/core/helper/src/aba_installer/install.yml (env-var block).
#   ABA_REF=your-feature-branch "./ABA Setup.command"
# (ABA_REPO_BRANCH / ABA_RECIPES_BRANCH are accepted as back-compat aliases.)
# Only affects the initial clone — on subsequent runs this script `git pull`s
# whatever the local checkout is on, so to switch after the fact, remove
# $REPO_DIR/aba and re-run. To keep the pin across `aba update`, set ABA_REF in
# ~/.aba/config.env (the launcher sources it).
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

# ── failure handling ───────────────────────────────────────────────────────
# The bootstrap runs in Terminal before the helper / web UI / sign-in / agent
# exist, so terminal text is the ONLY feedback. Every abort must say what broke
# and what to do, and must NOT auto-close the window (that happens only on a
# clean finish, gated by SETUP_DONE).
SETUP_DONE=0
fail() {  # $@: message lines — branded, actionable, then abort.
  echo >&2
  echo "✖ ABA Setup failed." >&2
  printf '   %s\n' "$@" >&2
  exit 1
}
on_err() {  # ERR trap for UNanticipated aborts (set -e). $1 = failing line.
  local rc=$? ln="${1:-?}"
  [ "$SETUP_DONE" = 1 ] && return 0
  echo >&2
  echo "✖ ABA Setup failed (line $ln, exit $rc)." >&2
  echo "   See the messages above. Fix the issue and re-run this installer." >&2
  echo "   If the helper started, its logs are under $HELPER_DIR/." >&2
}
trap 'on_err $LINENO' ERR

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
clone_or_pull() {  # $1=url  $2=dest  $3=ref (branch/tag/commit, optional)
  local name; name="$(basename "$2")"
  if [[ -d "$2/.git" ]]; then
    echo "Updating $name …"
    # Don't abort the whole install on a non-fast-forward; warn and keep the
    # existing checkout (better than dead-ending an otherwise-working setup).
    git -C "$2" pull --ff-only \
      || echo "  warning: could not fast-forward $name; continuing with the existing checkout." >&2
  else
    echo "Cloning $name …"
    # `|| rc=$?` keeps set -e from aborting before we can give a useful message.
    local rc=0
    if [[ -n "${3:-}" ]]; then
      # $3 pins a branch/tag/commit. Shallow --branch covers branch+tag; a bare
      # commit SHA falls back to a full clone + checkout (matches the playbook).
      git clone -b "$3" --depth 1 "$1" "$2" 2>/dev/null \
        || { rm -rf "$2"; git clone "$1" "$2" && git -C "$2" checkout "$3"; } || rc=$?
    else
      git clone --depth 1 "$1" "$2" || rc=$?
    fi
    if [[ "$rc" != 0 ]]; then
      fail "Could not clone $1" \
           "If the ABA repositories are still private, clone over SSH with your own access:" \
           "    ABA_REPO_URL=git@github.com:kharchenkolab/aba.git \\" \
           "    ABA_RECIPES_URL=git@github.com:kharchenkolab/aba-recipe-pack.git \\" \
           "        bash \"$0\"" \
           "Otherwise: check your network, that 'git --version' works, and that the" \
           "Xcode Command Line Tools are installed (xcode-select --install)."
    fi
  fi
}
# ABA_REF / RECIPES_REF pin the clone (branch/tag/commit); ABA_REPO_BRANCH /
# ABA_RECIPES_BRANCH are back-compat aliases. Empty → the remote's default branch.
ABA_REF="${ABA_REF:-${ABA_REPO_BRANCH:-}}"
RECIPES_REF="${RECIPES_REF:-${ABA_RECIPES_BRANCH:-}}"
clone_or_pull "$ABA_REPO_URL"    "$REPO_DIR/aba"             "$ABA_REF"
clone_or_pull "$ABA_RECIPES_URL" "$REPO_DIR/aba-recipe-pack" "$RECIPES_REF"

# Install the helper from the cloned repo into a private venv (never touches
# the user's system Python).
if [[ ! -x "$HELPER_DIR/venv/bin/python" ]]; then
  echo "Creating helper venv …"
  "$PY" -m venv "$HELPER_DIR/venv"
fi
"$HELPER_DIR/venv/bin/pip" install --quiet --upgrade pip
# NOT --quiet: if this fails (network, build error), the pip output is the only
# clue the user (or we) get — the ERR trap aborts with it visible.
"$HELPER_DIR/venv/bin/pip" install "$REPO_DIR/aba/install/core/helper"

# Render + load the LaunchAgent so the helper auto-starts on login — and
# starts it now (RunAtLoad). The plist is a template needing path
# substitution, so we render it through the helper's own code. If that fails
# on this Mac, fall back to starting the helper directly.
export ABA_HOME
# Env-build strategy (misc/lazy_env_init.md): a personal Mac defaults to `staged`
# — start the server on a minimal base, then finish the scientific Python stack +
# R env in the background while the user works. Persist to config.env (the runtime
# + `aba update` read it) and export so the install service's playbook sees it.
# Override by exporting ABA_ENV_PREWARM=eager before running.
export ABA_ENV_PREWARM="${ABA_ENV_PREWARM:-staged}"
mkdir -p "$ABA_HOME"; touch "$ABA_HOME/config.env"
grep -q '^ABA_ENV_PREWARM=' "$ABA_HOME/config.env" 2>/dev/null \
  || echo "ABA_ENV_PREWARM=$ABA_ENV_PREWARM" >> "$ABA_HOME/config.env"
# Subscription sign-in (Settings → Agent): a personal Mac is local, so both the
# Anthropic paste flow and OpenAI's localhost:1455 callback work — let the user
# connect a Claude.ai / ChatGPT-Codex plan instead of pasting an API key.
grep -q '^ABA_SUBSCRIPTION_OAUTH=' "$ABA_HOME/config.env" 2>/dev/null \
  || echo "ABA_SUBSCRIPTION_OAUTH=1" >> "$ABA_HOME/config.env"
if ! "$HELPER_DIR/venv/bin/python" -c \
     "from aba_installer.launchagent import install_launch_agent; install_launch_agent()"; then
  echo "Warning: could not install the auto-start LaunchAgent; starting the helper directly for this session." >&2
  nohup "$HELPER_DIR/venv/bin/python" -m aba_installer.service \
    >> "$HELPER_DIR/helper.out.log" 2>&1 &
fi

# Tier-0-tray (misc/mac-install.md § 3c): install ABA.app into
# ~/Applications + register the tray LaunchAgent. Now ON by default on macOS
# (the menu-bar app is the primary way users start/stop ABA); opt OUT with
# ABA_INSTALL_TRAY=0. Failures are non-fatal — the rest of the install still
# completes and the browser control panel still works.
if [[ "$(uname -s)" == "Darwin" && "${ABA_INSTALL_TRAY:-1}" != "0" ]]; then
  echo "Installing ABA Tray …"
  "$HELPER_DIR/venv/bin/python" -m aba_installer.tray_install || \
    echo "Warning: ABA Tray install failed; continuing without the menu-bar app." >&2
fi

# Wait for the helper to come up, then open the browser. A helper that never
# readies must be a hard failure — NOT a silent fall-through that opens a dead
# page and claims success (the old behaviour).
echo "Starting helper …"
PORT=8765
ready=0
for _ in $(seq 1 60); do
  if [[ -f "$HELPER_DIR/port.txt" ]]; then
    PORT="$(cat "$HELPER_DIR/port.txt")"
  fi
  if curl -fs "http://127.0.0.1:$PORT/ready" > /dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.5
done
if [[ "$ready" != 1 ]]; then
  fail "The ABA helper did not come up within 30s." \
       "Look at the helper logs for the cause:" \
       "    $HELPER_DIR/helper.err.log" \
       "    $HELPER_DIR/helper.out.log" \
       "Fix the issue (or report it with those logs) and re-run this installer."
fi

# Use localhost (not 127.0.0.1) so the UI tab shares an origin with the OAuth
# callback (http://localhost:PORT/callback) — lets the callback advance the
# Setup tab after Sign in with Claude.ai.
URL="http://localhost:$PORT/"
SETUP_DONE=1   # past the point of failure — success path; ERR footer now silenced
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
