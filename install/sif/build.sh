#!/usr/bin/env bash
# Build the ABA Apptainer image — one script, two profiles.
#
#   ./install/sif/build.sh --profile fat        # bakes conda venv + R base + backend + frontend + recipes
#   ./install/sif/build.sh --profile slim       # bakes backend + frontend + recipes; venv + R base mounted at run
#
# Recipes are baked as the SYSTEM bundle in BOTH profiles (ABA_SYSTEM_BUNDLE) — the
# image ships the curated pack; institution/lab/user bundles layer on top from
# site.yaml at run time (no rebuild). The env specs come from install/core (one
# source of truth with the mac/linux installers).
#
# Env knobs: APPTAINER (binary), APPTAINER_TMPDIR, MICROMAMBA (fat only),
# ABA_RECIPES_SRC (pack dir), ABA_SIF_OUT (output path), ABA_SIF_STAGE (stage dir).
# Accelerator (fat only, F1): ABA_ACCELERATOR=cpu|cuda (default cpu) + ABA_CUDA_VERSION
# (default 11.8) — when cuda, bakes the GPU torch base (inject-accelerator + CONDA_OVERRIDE_CUDA),
# so a GPU OOD/SIF deploy doesn't silently get CPU torch. Recorded in the image's %labels.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="fat"; OUT=""; STAGE_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:?}"; shift ;;
    --out)     OUT="${2:?}"; shift ;;
    --stage-only) STAGE_ONLY=1 ;;     # stage + generate the .def, skip the apptainer build (for CI / inspection)
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac; shift
done
case "$PROFILE" in fat|slim) ;; *) echo "--profile must be fat or slim"; exit 2 ;; esac
ACCEL="${ABA_ACCELERATOR:-cpu}"   # F1: bakes the GPU torch base when 'cuda' (fat profile)

APPTAINER="${APPTAINER:-apptainer}"
STAGE="${ABA_SIF_STAGE:-$REPO_ROOT/../tools/stage-$PROFILE}"
OUT="${OUT:-${ABA_SIF_OUT:-$REPO_ROOT/../tools/aba-$PROFILE.sif}}"
PACK="${ABA_RECIPES_SRC:-/tmp/aba-recipe-pack}"

echo "== ABA SIF build =="
echo "   profile: $PROFILE   out: $OUT"
echo "   stage:   $STAGE"

# ── stage backend + frontend (clean of the repo's dangling/cyclic symlinks) ──
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -a "$REPO_ROOT/backend" "$STAGE/backend"
cp -a "$REPO_ROOT/frontend/dist" "$STAGE/frontend-dist"
# drop broken links, then ancestor-cycle links (.claude -> .) that cp -fLr chokes on
find "$STAGE" -type l | while read -r l; do readlink -e "$l" >/dev/null 2>&1 || rm -f "$l"; done
find "$STAGE" -type l | while read -r l; do
  d=$(readlink -f "$(dirname "$l")"); t=$(readlink -f "$l")
  [ -d "$t" ] && case "$d/" in "$t/"*) rm -f "$l" ;; esac
done

# ── bake recipes as the SYSTEM bundle (built-in skills + the recipe pack) ──
SB="$STAGE/system_bundle"; mkdir -p "$SB/skills/recipes" "$SB/catalog"
if [ -d "$REPO_ROOT/backend/system_bundle" ]; then cp -a "$REPO_ROOT/backend/system_bundle/." "$SB/" 2>/dev/null || true; fi
if [ -d "$PACK/recipes" ]; then cp -R "$PACK/recipes/." "$SB/skills/recipes/"; else echo "WARNING: no recipe pack at $PACK (set ABA_RECIPES_SRC)"; fi
if ls "$PACK"/catalog/*.yaml >/dev/null 2>&1; then cp "$PACK"/catalog/*.yaml "$SB/catalog/"; fi
echo "   recipes baked: $(find "$SB/skills/recipes" -name '*.md' 2>/dev/null | wc -l | tr -d ' ') files, $(ls "$SB/catalog"/*.yaml 2>/dev/null | wc -l | tr -d ' ') catalog"

# ── bake the OOD preflight so before.sh.erb runs it FROM the image (version-
# locked to the backend; no dev-path surgery on the launch card) ──
mkdir -p "$STAGE/ood"; cp "$REPO_ROOT/install/ood/aba_preflight.py" "$STAGE/ood/aba_preflight.py"
echo "   ood preflight baked: /opt/aba/ood/aba_preflight.py"

# ── bake the pagoda3 viewer dist (grabbed from its GitHub release — a pre-built
# static bundle, NOT vendored/built here; the /pagoda3 mount serves it). Unzipped
# on the build host (needs curl+unzip); the image just gets the files. Non-fatal:
# a fetch failure leaves the interactive viewer unavailable (conversion still works).
PG3_URL="${ABA_PAGODA3_DIST_URL:-https://github.com/kharchenkolab/pagoda3/releases/download/v0.1.0/pagoda3-viewer-0.1.0.zip}"
if command -v unzip >/dev/null 2>&1 && curl -fsSL "$PG3_URL" -o "$STAGE/pg3.zip" 2>/dev/null; then
  mkdir -p "$STAGE/pagoda3-dist"
  if unzip -q -o "$STAGE/pg3.zip" -d "$STAGE/pagoda3-dist" 2>/dev/null && [ -f "$STAGE/pagoda3-dist/index.html" ]; then
    echo "   pagoda3 viewer dist baked → /opt/aba/vendor/pagoda3/dist"
  else rm -rf "$STAGE/pagoda3-dist"; echo "WARNING: pagoda3 dist unzip produced no index.html — interactive viewer unavailable"; fi
  rm -f "$STAGE/pg3.zip"
else echo "NOTE: pagoda3 dist not baked (curl+unzip needed / fetch failed) — interactive viewer unavailable"; fi

# ── runtime-install essentials the debian:12-slim base omits (no %post needed) ──
# CA certs: without them every runtime https download (pip/PyPI, micromamba, CRAN/
# Bioconductor) fails with CERTIFICATE_VERIFY_FAILED. micromamba: the agent needs
# it to materialize conda/CLI tools into the per-user growth env at run time.
for c in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
  [ -f "$c" ] && { cp "$c" "$STAGE/ca-certificates.crt"; break; }
done
[ -f "$STAGE/ca-certificates.crt" ] && echo "   CA certs baked" || echo "WARNING: no host CA bundle found to bake"
MM_BIN="${MICROMAMBA:-$(command -v micromamba 2>/dev/null || true)}"
if [ -x "$MM_BIN" ]; then mkdir -p "$STAGE/bin"; cp "$MM_BIN" "$STAGE/bin/micromamba"; echo "   micromamba baked"; else echo "NOTE: no micromamba to bake (runtime conda installs unavailable)"; fi

# ── fat: build the conda venv + R tools base from install/core specs ──
if [ "$PROFILE" = "fat" ]; then
  MM="${MICROMAMBA:-micromamba}"
  command -v "$MM" >/dev/null 2>&1 || { echo "fat build needs micromamba (set MICROMAMBA)"; exit 1; }
  # Deployment-conditional base (F1 fix): mirror the installer's create-env. Inject the
  # CUDA torch pin when ABA_ACCELERATOR=cuda so the BAKED venv resolves the GPU torch build
  # — else conda-forge bakes CPU-only torch and GPU jobs silently run on CPU (the scVI-on-CPU
  # incident). Inject into a COPY so the shared install/core/environment.yml is never mutated.
  ENV_YML="$STAGE/environment.yml"
  cp "$REPO_ROOT/install/core/environment.yml" "$ENV_YML"
  bash "$REPO_ROOT/install/core/inject-accelerator.sh" "$ENV_YML"
  if [ "$ACCEL" = "cuda" ]; then
    # the build host has no GPU → spoof __cuda so the GPU solve resolves (same as the installer)
    export CONDA_OVERRIDE_CUDA="${ABA_CUDA_VERSION:-11.8}"
    echo "-- [accelerator] CUDA base: CONDA_OVERRIDE_CUDA=$CONDA_OVERRIDE_CUDA --"
  fi
  echo "-- building conda venv ($ACCEL base, from $ENV_YML) --"
  "$MM" create -y -q --channel-priority strict -p "$STAGE/aba-venv" -f "$ENV_YML"
  echo "-- building R tools base (install/core/r-environment.yml) --"
  "$MM" create -y -q -p "$STAGE/aba-tools" -f "$REPO_ROOT/install/core/r-environment.yml" \
    || echo "WARNING: R tools base failed — R will provision on demand"
fi

# ── generate the .def for this profile ──
DEF="$STAGE/aba-$PROFILE.def"
{
  echo "Bootstrap: docker"
  echo "From: debian:12-slim"
  echo ""
  echo "# Generated by install/sif/build.sh --profile $PROFILE. Do not edit."
  echo "%files"
  echo "    $STAGE/backend /opt/aba/backend"
  echo "    $STAGE/frontend-dist /opt/aba/frontend-dist"
  echo "    $STAGE/system_bundle /opt/aba/system_bundle"
  echo "    $STAGE/ood/aba_preflight.py /opt/aba/ood/aba_preflight.py"
  [ -d "$STAGE/pagoda3-dist" ] && echo "    $STAGE/pagoda3-dist /opt/aba/vendor/pagoda3/dist"
  [ -f "$STAGE/ca-certificates.crt" ] && echo "    $STAGE/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt"
  [ -d "$STAGE/bin" ] && echo "    $STAGE/bin /opt/aba/bin"
  [ "$PROFILE" = "fat" ] && echo "    $STAGE/aba-venv /opt/aba-venv"
  [ "$PROFILE" = "fat" ] && echo "    $STAGE/aba-tools /opt/aba-envs/tools"
  echo ""
  echo "%environment"
  echo "    export ABA_SYSTEM_BUNDLE=/opt/aba/system_bundle"
  echo "    export ABA_FRONTEND_DIST=\${ABA_FRONTEND_DIST:-/opt/aba/frontend-dist}"
  # pagoda3 viewer dist (baked above) — point the launcher's resolver at it so the
  # /pagoda3 mount wires up. Absent → the interactive viewer is unavailable (graceful).
  [ -d "$STAGE/pagoda3-dist" ] && echo "    export ABA_PAGODA3_DIST=\${ABA_PAGODA3_DIST:-/opt/aba/vendor/pagoda3/dist}"
  # /opt/aba-venv + /opt/aba-envs/tools are BAKED (fat) or BIND-MOUNTED (slim) —
  # same paths either way, so the runscript + env don't branch on the profile.
  # /opt/aba/bin carries the baked micromamba (runtime conda/CLI materialization).
  echo "    export PATH=/opt/aba/bin:/opt/aba-venv/bin:\$PATH"
  echo "    export ABA_TOOLS_DIR=\${ABA_TOOLS_DIR:-/opt/aba-envs/tools}"
  echo "    export SSL_CERT_FILE=\${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
  echo "    export REQUESTS_CA_BUNDLE=\${REQUESTS_CA_BUNDLE:-/etc/ssl/certs/ca-certificates.crt}"
  echo ""
  echo "%runscript"
  echo "    export HOME=\"\${ABA_RUNTIME_DIR:-/tmp/aba}/.home\"; mkdir -p \"\$HOME\""
  echo "    cd /opt/aba/backend"
  echo "    exec /opt/aba-venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port \"\${ABA_PORT:-8000}\""
  echo ""
  echo "%labels"
  echo "    org.aba.role ondemand-backend"
  echo "    org.aba.profile $PROFILE"
  # F1: record the baked accelerator so a CPU image can't be mistaken for GPU
  # (fat bakes the venv; slim mounts it at runtime, so the label is authoritative for fat).
  echo "    org.aba.accelerator $ACCEL"
  [ "$ACCEL" = "cuda" ] && echo "    org.aba.cuda_version ${ABA_CUDA_VERSION:-11.8}"
} > "$DEF"
echo "   wrote $DEF"

if [ "$STAGE_ONLY" = "1" ]; then echo "stage-only: skipping apptainer build"; exit 0; fi

# ── build ──
echo "-- apptainer build --"
"$APPTAINER" build --force "$OUT" "$DEF"
echo "✓ built $OUT ($(du -h "$OUT" 2>/dev/null | cut -f1))"
