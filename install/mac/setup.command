#!/usr/bin/env bash
# ABA Setup — bootstrap installer for macOS.
#
# This file is distributed as ABA-Setup.zip on github.com/kharchenkolab/aba/releases.
# When the user double-clicks it: Terminal opens, this script downloads the
# helper service, starts it on localhost, opens the browser, and exits.
# Everything else happens in the browser UI.
#
# What this script touches:
#   ~/.aba/installer/                              (helper service + state)
#   ~/Library/LaunchAgents/com.kharchenkolab.aba.helper.plist  (auto-start on login)
#
# Anything heavier (Python env, R, the repo) is downloaded by the helper
# only after the user clicks "Install ABA" in the browser UI.

set -euo pipefail

HELPER_URL="${HELPER_URL:-https://github.com/kharchenkolab/aba/releases/latest/download/helper-latest.tgz}"
ABA_HOME="$HOME/.aba"
HELPER_DIR="$ABA_HOME/installer"

cat <<EOF
ABA Setup
─────────
Installing the lightweight ABA helper into:
    $HELPER_DIR

This file is ~5 MB and runs on localhost. Nothing else is touched until you
click "Install ABA" in the browser.

EOF

# Pre-flight checks
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Error: ABA Setup runs on macOS. Detected: $(uname -s)" >&2
  exit 1
fi

mkdir -p "$HELPER_DIR"

# Download (or refresh) the helper tarball
echo "Downloading ABA helper from $HELPER_URL …"
curl -fsSL "$HELPER_URL" -o "$HELPER_DIR/helper.tgz"
echo "Extracting …"
# Extract into a versioned subdir so we never overwrite a running helper
STAMP="$(date +%s)"
EXTRACT_DIR="$HELPER_DIR/helper-$STAMP"
mkdir -p "$EXTRACT_DIR"
tar -xzf "$HELPER_DIR/helper.tgz" -C "$EXTRACT_DIR"
ln -sfn "$EXTRACT_DIR" "$HELPER_DIR/current"

# Make sure system python3 has the helper's deps. Use a private venv under
# HELPER_DIR so we never touch the user's system Python.
PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "Error: macOS 12.3+ ships with python3; could not find it on this system." >&2
  exit 1
fi

if [[ ! -x "$HELPER_DIR/venv/bin/python" ]]; then
  echo "Creating helper venv …"
  "$PY" -m venv "$HELPER_DIR/venv"
fi
"$HELPER_DIR/venv/bin/pip" install --quiet --upgrade pip
"$HELPER_DIR/venv/bin/pip" install --quiet "$HELPER_DIR/current"

# Install (or update) the LaunchAgent so the helper auto-starts on login.
# We do this AFTER the venv is built so the first launch can find python.
PLIST_SRC="$HELPER_DIR/current/com.kharchenkolab.aba.helper.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.kharchenkolab.aba.helper.plist"
if [[ -f "$PLIST_SRC" ]]; then
  mkdir -p "$HOME/Library/LaunchAgents"
  cp "$PLIST_SRC" "$PLIST_DST"
  launchctl unload "$PLIST_DST" 2>/dev/null || true
  launchctl load -w "$PLIST_DST"
fi

# Wait for the helper to come up
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

URL="http://127.0.0.1:$PORT/"
echo
echo "ABA Setup is running."
echo "Opening $URL in your browser…"
open "$URL" || true

# Best-effort: close this Terminal window so the user isn't left staring at a
# blank prompt. Quietly ignored if the user's Terminal is not the default.
osascript <<APPLESCRIPT 2>/dev/null || true
tell application "Terminal"
  set windowList to (every window whose name contains "ABA Setup" or name contains "setup.command")
  repeat with w in windowList
    close w saving no
  end repeat
end tell
APPLESCRIPT

exit 0
