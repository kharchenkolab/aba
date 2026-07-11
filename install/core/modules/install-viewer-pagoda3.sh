#!/usr/bin/env bash
# Module: viewer-pagoda3 — install the pagoda3 interactive viewer dist (the built
# browser bundle attached to the pagoda3 GitHub release). Version-aware + idempotent:
# a marker records the installed URL, so a re-run with the same version is a no-op and
# a failed download keeps any working dist (the viewer never goes dark mid-update).
# Installs the pagoda3 Python reader (lstar-sc / zarr) into the base env — it's a
# MODULE dep now, not core (misc/modules.md Phase 6) — then the viewer dist.
#
# Inputs (env, with defaults):
#   ABA_HOME              install root (default ~/.aba)
#   ENV_DIR               base conda env prefix (default $ABA_HOME/env)
#   ABA_PAGODA3_DIST_URL  release zip URL override
#   ABA_LSTAR_SC_PIN      lstar-sc version pin (default lstar-sc==0.2.1)
set -uo pipefail

ABA_HOME="${ABA_HOME:-$HOME/.aba}"
ENV_DIR="${ENV_DIR:-$ABA_HOME/env}"
LSTAR_PIN="${ABA_LSTAR_SC_PIN:-lstar-sc==0.2.1}"
DIST="$ABA_HOME/vendor/pagoda3/dist"

# 1) The Python reader (lstar-sc + zarr 3) into the base env. Co-versioned with the
#    dist below — bump both together. chmod u+w first (base is linked read-only).
if [ -x "$ENV_DIR/bin/python" ]; then
  chmod -R u+w "$ENV_DIR"/lib/python*/site-packages 2>/dev/null || true
  if PYTHONNOUSERSITE=1 PIP_USER=0 "$ENV_DIR/bin/python" -m pip install --prefer-binary \
       "$LSTAR_PIN" "zarr>=3.1"; then
    echo "[viewer-pagoda3] installed reader ($LSTAR_PIN, zarr>=3.1) into the base env"
  else
    echo "[viewer-pagoda3] WARNING: reader install failed — .lstar.zarr conversion/viewing degraded" >&2
  fi
else
  echo "[viewer-pagoda3] WARNING: base env python not found at $ENV_DIR — skipping reader install" >&2
fi

# 2) The viewer dist (built browser bundle) from the pagoda3 release.
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
