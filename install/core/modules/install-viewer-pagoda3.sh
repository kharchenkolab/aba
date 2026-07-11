#!/usr/bin/env bash
# Module: viewer-pagoda3 — install the pagoda3 interactive viewer dist (the built
# browser bundle attached to the pagoda3 GitHub release). Version-aware + idempotent:
# a marker records the installed URL, so a re-run with the same version is a no-op and
# a failed download keeps any working dist (the viewer never goes dark mid-update).
# Phase 6 (misc/modules.md) also installs the lstar-sc reader here once it's pulled
# out of the core base.
#
# Inputs (env, with defaults):
#   ABA_HOME              install root (default ~/.aba)
#   ABA_PAGODA3_DIST_URL  release zip URL override
set -uo pipefail

ABA_HOME="${ABA_HOME:-$HOME/.aba}"
DIST="$ABA_HOME/vendor/pagoda3/dist"
URL="${ABA_PAGODA3_DIST_URL:-https://github.com/kharchenkolab/pagoda3/releases/download/v0.2.1/pagoda3-viewer-0.2.1.zip}"
MARK="$DIST/.aba-dist-url"

if [ -f "$DIST/index.html" ] && [ "$(cat "$MARK" 2>/dev/null)" = "$URL" ]; then
  echo "[viewer-pagoda3] dist already present ($URL) — skipping"; exit 0
fi
if ! command -v unzip >/dev/null 2>&1; then
  echo "[viewer-pagoda3] ERROR: unzip not found — cannot install the dist" >&2; exit 1
fi

TMP="$(mktemp -d)"
if curl -fsSL "$URL" -o "$TMP/pg3.zip" \
   && unzip -q -o "$TMP/pg3.zip" -d "$TMP/x" \
   && [ -f "$TMP/x/index.html" ]; then
  rm -rf "$DIST"; mkdir -p "$(dirname "$DIST")"; mv "$TMP/x" "$DIST"
  printf '%s' "$URL" > "$MARK"
  echo "[viewer-pagoda3] dist installed to $DIST ($URL)"
  rm -rf "$TMP"; exit 0
else
  echo "[viewer-pagoda3] WARNING: dist fetch/unzip failed ($URL) — keeping any existing dist" >&2
  rm -rf "$TMP"; exit 1
fi
