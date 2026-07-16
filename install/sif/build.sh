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
# Base image (--base / ABA_SIF_BASE, default oraclelinux:7; ABA_SIF_BOOTSTRAP, default docker):
# CRITICAL — the base glibc MUST be <= your compute nodes' glibc (match the cluster OS
# family), else in-container-compiled tools fail on the nodes + host modules won't load.
# Accelerator (fat only, F1): ABA_ACCELERATOR=cpu|cuda (default cpu) + ABA_CUDA_VERSION
# (default 11.8) — when cuda, bakes the GPU torch base (inject-accelerator + CONDA_OVERRIDE_CUDA),
# so a GPU OOD/SIF deploy doesn't silently get CPU torch. Recorded in the image's %labels.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROFILE="fat"; OUT=""; STAGE_ONLY=0; SIF_BASE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="${2:?}"; shift ;;
    --out)     OUT="${2:?}"; shift ;;
    --base)    SIF_BASE="${2:?}"; shift ;;   # base image (.def From:) — glibc MUST be <= your compute nodes'
    --stage-only) STAGE_ONLY=1 ;;     # stage + generate the .def, skip the apptainer build (for CI / inspection)
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac; shift
done
case "$PROFILE" in fat|slim) ;; *) echo "--profile must be fat or slim"; exit 2 ;; esac
ACCEL="${ABA_ACCELERATOR:-cpu}"   # F1: bakes the GPU torch base when 'cuda' (fat profile)
# Base image (the .def `From:`). It supplies ONLY the root filesystem (glibc, shell,
# coreutils) — there is NO %post, so no package manager runs; CA certs + micromamba are
# baked from the host, and the conda env is baked (fat) or mounted (slim). The ONE hard
# rule: the base glibc MUST be <= your compute nodes' glibc, or (a) tools compiled inside
# the container fail on the nodes and (b) host environment-modules (Lmod) won't load in it.
# conda-forge itself builds to a glibc-2.17 (EL7) floor, so an EL7 base is the portable,
# cluster-matching default. Override with --base / ABA_SIF_BASE (+ ABA_SIF_BOOTSTRAP).
SIF_BASE="${SIF_BASE:-${ABA_SIF_BASE:-oraclelinux:7}}"
SIF_BOOTSTRAP="${ABA_SIF_BOOTSTRAP:-docker}"

APPTAINER="${APPTAINER:-apptainer}"
STAGE="${ABA_SIF_STAGE:-$REPO_ROOT/../tools/stage-$PROFILE}"
OUT="${OUT:-${ABA_SIF_OUT:-$REPO_ROOT/../tools/aba-$PROFILE.sif}}"
PACK="${ABA_RECIPES_SRC:-/tmp/aba-recipe-pack}"

echo "== ABA SIF build =="
echo "   profile: $PROFILE   base: $SIF_BOOTSTRAP://$SIF_BASE   out: $OUT"
echo "   stage:   $STAGE"

# ── stage backend + frontend (clean of the repo's dangling/cyclic symlinks) ──
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -a "$REPO_ROOT/backend" "$STAGE/backend"
cp -a "$REPO_ROOT/frontend/dist" "$STAGE/frontend-dist"
# The SIF is the OOD/cluster artifact, and OOD serves the SPA under /rnode/<host>/<port>/.
# The dist must therefore carry the /__OOD_PREFIX__/ base placeholder that script.sh.erb
# rewrites per session (build the frontend with ABA_OOD_BASE=/__OOD_PREFIX__/). A dist built
# with vite's default base='/' points its assets at the DASHBOARD root: the backend is healthy,
# nothing errors, and the browser just renders a BLANK page. Warn loudly at build, not never.
if ! grep -q '__OOD_PREFIX__' "$STAGE/frontend-dist/index.html" 2>/dev/null; then
  echo "WARNING: frontend-dist/index.html has no __OOD_PREFIX__ placeholder — assets are rooted at '/'." >&2
  echo "         Behind the OOD proxy this serves a BLANK page. Rebuild the frontend with:" >&2
  echo "           cd frontend && ABA_OOD_BASE='/__OOD_PREFIX__/' npx vite build" >&2
fi
# drop broken links, then ancestor-cycle links (.claude -> .) that cp -fLr chokes on
find "$STAGE" -type l | while read -r l; do readlink -e "$l" >/dev/null 2>&1 || rm -f "$l"; done
find "$STAGE" -type l | while read -r l; do
  d=$(readlink -f "$(dirname "$l")"); t=$(readlink -f "$l")
  [ -d "$t" ] && case "$d/" in "$t/"*) rm -f "$l" ;; esac
done

# ── bake the module manifests (install/core/modules/*.yaml + install-*.sh) ──
# The modules registry (core/modules/registry.py) discovers manifests at
# <repo>/install/core/modules — which in the image is $backend/../../install/core/modules
# = /opt/aba/install/core/modules (parents[3] of registry.py at /opt/aba/backend/...).
# Without these baked, reg.ids() is EMPTY in the image: no modules catalog, so the
# Settings→Modules UI is blank and first-use gating is a no-op. Bake the whole dir
# (manifests + their install-*.sh, which install_script resolves relative to) so the
# registry populates and readiness probes run. (Install scripts never FIRE in a fat
# deploy — everything's baked + probes ready — but a valid path keeps the spec well-formed.)
if [ -d "$REPO_ROOT/install/core/modules" ]; then
  mkdir -p "$STAGE/modules"; cp -a "$REPO_ROOT/install/core/modules/." "$STAGE/modules/"
  echo "   module manifests baked: $(ls "$STAGE/modules"/*.yaml 2>/dev/null | wc -l | tr -d ' ')"
fi

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
PG3_URL="${ABA_PAGODA3_DIST_URL:-https://github.com/kharchenkolab/pagoda3/releases/download/v0.2.1/pagoda3-viewer-0.2.1.zip}"
if command -v unzip >/dev/null 2>&1 && curl -fsSL "$PG3_URL" -o "$STAGE/pg3.zip" 2>/dev/null; then
  mkdir -p "$STAGE/pagoda3-dist"
  if unzip -q -o "$STAGE/pg3.zip" -d "$STAGE/pagoda3-dist" 2>/dev/null && [ -f "$STAGE/pagoda3-dist/index.html" ]; then
    echo "   pagoda3 viewer dist baked → /opt/aba/vendor/pagoda3/dist"
  else rm -rf "$STAGE/pagoda3-dist"; echo "WARNING: pagoda3 dist unzip produced no index.html — interactive viewer unavailable"; fi
  rm -f "$STAGE/pg3.zip"
else echo "NOTE: pagoda3 dist not baked (curl+unzip needed / fetch failed) — interactive viewer unavailable"; fi

# ── bake essentials a minimal base image omits (no %post needed; base-family-agnostic) ──
# CA certs: without them every runtime https download (pip/PyPI, micromamba, CRAN/
# Bioconductor) fails with CERTIFICATE_VERIFY_FAILED. micromamba: the agent needs
# it to materialize conda/CLI tools into the per-user growth env at run time.
for c in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
  [ -f "$c" ] && { cp "$c" "$STAGE/ca-certificates.crt"; break; }
done
[ -f "$STAGE/ca-certificates.crt" ] && echo "   CA certs baked" || echo "WARNING: no host CA bundle found to bake"
# `which`: EL8/EL9/EL10 minimal base images DROPPED it (EL7 shipped it, which is why the
# old oraclelinux:7 default never hit this). R's `utils` package shells out to it from
# .onLoad during loadNamespace(), so WITHOUT it every R kernel start dies with:
#   Error: .onLoad failed in loadNamespace() for 'utils', details:
#     call: system(paste(which, shQuote(names[i])), intern = TRUE, ignore.stderr = TRUE)
# It resolves the ABSOLUTE path /usr/bin/which — putting a `which` on PATH does NOT
# satisfy it (verified). There is no %post to dnf-install one (rootless builds have no
# fakeroot), and copying the host binary would bind the image to the build host's glibc,
# so ship a dependency-free POSIX shim.
cat > "$STAGE/which" <<'WHICH_SHIM'
#!/bin/sh
# Minimal `which` — the base image omits it (EL8+). Prints the absolute path of each
# external command, exits non-zero if any is not found. Shell builtins are NOT reported
# as found, matching which(1) closely enough for R's utils/.onLoad probe.
_st=0
for _a in "$@"; do
  case "$_a" in -*) continue ;; esac
  _p=$(command -v "$_a" 2>/dev/null) || { _st=1; continue; }
  case "$_p" in /*) printf '%s\n' "$_p" ;; *) _st=1 ;; esac
done
exit $_st
WHICH_SHIM
chmod 0755 "$STAGE/which"
echo "   which shim baked (base images since EL8 omit it; R's utils needs /usr/bin/which)"
MM_BIN="${MICROMAMBA:-$(command -v micromamba 2>/dev/null || true)}"
if [ -x "$MM_BIN" ]; then mkdir -p "$STAGE/bin"; cp "$MM_BIN" "$STAGE/bin/micromamba"; echo "   micromamba baked"; else echo "NOTE: no micromamba to bake (runtime conda installs unavailable)"; fi

# ── bake the WEFT compute substrate (weft rewrite W3.4/W3.5): pixi binaries +
# the weft package + the base env packs. On a weft deployment the science env is
# a weft SESSION over a base pack (not the served conda venv); the SIF must carry
# the substrate so the controller can solve/realize/adopt on the site. pixi lands
# where resolve_pixi() looks ($ABA_HOME/tools/pixi/bin → /opt/aba/tools/pixi/bin);
# weft is pip-installed into the venv below (fat) or the mounted venv (slim). The
# base packs go to the INSTALLATION scope (operator-owned; base_env composes the
# envs/ facet from there) — NOT the system bundle (domain stays out of platform).
PIXI_SRC="${ABA_PIXI_SRC:-$REPO_ROOT/../tools/pixi/bin}"
if [ -x "$PIXI_SRC/pixi" ]; then
  mkdir -p "$STAGE/pixi/bin"
  for b in pixi pixi-pack pixi-unpack; do [ -x "$PIXI_SRC/$b" ] && cp "$PIXI_SRC/$b" "$STAGE/pixi/bin/$b"; done
  echo "   pixi baked: $(ls "$STAGE/pixi/bin" | tr '\n' ' ')"
else echo "NOTE: no pixi at $PIXI_SRC (set ABA_PIXI_SRC) — weft solves unavailable in the image"; fi
WEFT_SRC="${ABA_WEFT_SRC:-$REPO_ROOT/../weft}"
if [ -d "$WEFT_SRC/src/weft" ]; then
  mkdir -p "$STAGE/weft/src"
  cp -a "$WEFT_SRC/src/weft" "$STAGE/weft/src/weft"
  [ -f "$WEFT_SRC/pyproject.toml" ] && cp "$WEFT_SRC/pyproject.toml" "$STAGE/weft/pyproject.toml"
  echo "   weft source staged (pip-installed into the venv below)"
else echo "NOTE: no weft checkout at $WEFT_SRC (set ABA_WEFT_SRC) — the substrate won't load in the image"; fi
if ls "$REPO_ROOT"/install/core/envs/*.yaml >/dev/null 2>&1; then
  mkdir -p "$STAGE/installation/envs"; cp "$REPO_ROOT"/install/core/envs/*.yaml "$STAGE/installation/envs/"
  echo "   base env packs baked: $(ls "$STAGE/installation/envs"/*.yaml | wc -l | tr -d ' ') (installation scope)"
fi

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
  # pip-install the weft substrate into the baked venv (W3.4). --no-user is
  # load-bearing on conda pythons (user-site is enabled → a bare pip would divert
  # to ~/.local and a user-site weft would shadow the image's). Non-fatal so a
  # transient failure doesn't abort the whole image.
  if [ -d "$STAGE/weft/src/weft" ]; then
    "$STAGE/aba-venv/bin/pip" install --no-input --no-user "$STAGE/weft" \
      && echo "   weft pip-installed into the baked venv" \
      || echo "WARNING: weft pip-install into the baked venv failed — substrate won't load"
  fi
  echo "-- building R tools base (install/core/r-environment.yml) --"
  "$MM" create -y -q -p "$STAGE/aba-tools" -f "$REPO_ROOT/install/core/r-environment.yml" \
    || echo "WARNING: R tools base failed — R will provision on demand"
  # bake the lstar R viewer bridge into the tools env (compiled here, travels in the image)
  # so Seurat/SCE .rds viewing works in the deploy — shared helper with the installers. The
  # helper rewrites the built .so rpath $ORIGIN-relative so it survives the stage→image move.
  bash "$REPO_ROOT/install/core/install-lstar-r.sh" "$STAGE/aba-tools" "$MM"
  # Mark the baked base COMPLETE so env_integrity.base_stage() reads `ready` explicitly
  # (it defaults to ready when the marker is absent, but a frozen image should be explicit:
  # the whole scientific stack is baked, so nothing must run staged base-completion at
  # runtime against the read-only mount). The python-bio module's readiness probe is
  # `base_stage: ready`, so this keeps it satisfied without any first-use env-update.
  printf 'ready\n' > "$STAGE/aba-venv/.aba-base-stage"
fi

# ── generate the .def for this profile ──
DEF="$STAGE/aba-$PROFILE.def"
{
  echo "Bootstrap: $SIF_BOOTSTRAP"
  echo "From: $SIF_BASE"
  echo ""
  echo "# Generated by install/sif/build.sh --profile $PROFILE. Do not edit."
  # %setup runs on the HOST as the build user, after the base is extracted and BEFORE
  # %files, with $APPTAINER_ROOTFS pointing at the extracted tree. That is the only hook
  # available to a rootless build: %post needs fakeroot, and %files cannot write into
  # /usr/bin because EL images ship it mode 0555 (we own it, but it is not writable).
  echo "%setup"
  echo "    # R's base::Sys.which() hardcodes the literal path \"/usr/bin/which\" (baked by"
  echo "    # R's configure) and has no file.exists() guard, so on a base without it EVERY"
  echo "    # Sys.which() call errors. utils::.onLoad -> .osVersion() -> Sys.which(\"uname\")"
  echo "    # then fails, loadNamespace('utils') fails, and NO R kernel can start. EL7 shipped"
  echo "    # /usr/bin/which; EL8/EL9/EL10 dropped it. Install a dependency-free POSIX shim."
  echo "    if [ ! -e \"\$APPTAINER_ROOTFS/usr/bin/which\" ]; then"
  echo "        _m=\$(stat -c %a \"\$APPTAINER_ROOTFS/usr/bin\")"
  echo "        chmod u+w \"\$APPTAINER_ROOTFS/usr/bin\""
  echo "        install -m 0755 \"$STAGE/which\" \"\$APPTAINER_ROOTFS/usr/bin/which\""
  echo "        chmod \"\$_m\" \"\$APPTAINER_ROOTFS/usr/bin\""
  echo "    fi"
  echo ""
  echo "%files"
  echo "    $STAGE/backend /opt/aba/backend"
  echo "    $STAGE/frontend-dist /opt/aba/frontend-dist"
  echo "    $STAGE/system_bundle /opt/aba/system_bundle"
  [ -d "$STAGE/modules" ] && echo "    $STAGE/modules /opt/aba/install/core/modules"
  echo "    $STAGE/ood/aba_preflight.py /opt/aba/ood/aba_preflight.py"
  [ -d "$STAGE/pagoda3-dist" ] && echo "    $STAGE/pagoda3-dist /opt/aba/vendor/pagoda3/dist"
  [ -f "$STAGE/ca-certificates.crt" ] && echo "    $STAGE/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt"
  [ -d "$STAGE/bin" ] && echo "    $STAGE/bin /opt/aba/bin"
  [ "$PROFILE" = "fat" ] && echo "    $STAGE/aba-venv /opt/aba-venv"
  [ "$PROFILE" = "fat" ] && echo "    $STAGE/aba-tools /opt/aba-envs/tools"
  # weft substrate: pixi binaries + the base env packs (weft is baked INTO the venv
  # above, so no separate copy). resolve_pixi() finds /opt/aba/tools/pixi/bin.
  [ -d "$STAGE/pixi/bin" ] && echo "    $STAGE/pixi/bin /opt/aba/tools/pixi/bin"
  [ -d "$STAGE/installation/envs" ] && echo "    $STAGE/installation/envs /opt/aba/installation/envs"
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
  echo "    export PATH=/opt/aba/bin:/opt/aba-venv/bin:/opt/aba/tools/pixi/bin:\$PATH"
  echo "    export ABA_TOOLS_DIR=\${ABA_TOOLS_DIR:-/opt/aba-envs/tools}"
  # weft substrate (W3.4/W3.5): the pixi solver, the per-deploy weft store, and the
  # base env packs (installation scope → base_env composes the envs/ facet). A
  # site.yaml can override ABA_INSTITUTION_BUNDLE to layer lab/user packs on top.
  [ -d "$STAGE/pixi/bin" ] && echo "    export ABA_PIXI_BIN=\${ABA_PIXI_BIN:-/opt/aba/tools/pixi/bin/pixi}"
  # (weft workspace needs no export: it derives as $ABA_HOME/weft, and with
  #  HOME redirected into ABA_RUNTIME_DIR below, that lands writable.)
  [ -d "$STAGE/installation/envs" ] && echo "    export ABA_INSTITUTION_BUNDLE=\${ABA_INSTITUTION_BUNDLE:-/opt/aba/installation}"
  # EAGER plugin state: the heavy modules (r-bio, viewer-pagoda3) are BAKED into this fat
  # image, so they should read as `on` (permanently present) rather than their `first_use`
  # registry default. manager._eager_override honors this; combined with the probe paths
  # above (ABA_TOOLS_DIR / ABA_PAGODA3_DIST) the reconciler sees them ready and installs
  # nothing at runtime. Fat only — slim mounts a shared base and keeps first-use semantics.
  [ "$PROFILE" = "fat" ] && echo "    export ABA_MODULES_EAGER=\${ABA_MODULES_EAGER:-python-bio r-bio viewer-pagoda3}"
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
  echo "    org.aba.base_image $SIF_BOOTSTRAP://$SIF_BASE"
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

# ── runtime prerequisites the R kernel needs (cheap, catches a lean base image) ──
# /usr/bin/which: R's utils .onLoad shells out to it; absent -> EVERY run_r dies at
# kernel registration with a ".onLoad failed in loadNamespace() for 'utils'" that names
# neither R nor `which`. Assert it here rather than discover it in a live session.
if ! "$APPTAINER" exec "$OUT" /usr/bin/which sh >/dev/null 2>&1; then
  echo "ERROR: /usr/bin/which is missing or broken in the image — R kernels will fail to start." >&2
  exit 1
fi
echo "   checked: /usr/bin/which present (R utils prerequisite)"

# ── glibc-floor check (install/sif/glibc-floor.sh): the base glibc must be <= the
# compute nodes'. Compare the built image to THIS build host (a proxy for the nodes —
# you build on/for the cluster). Loud but NON-FATAL — you may be building elsewhere.
# NB /usr/bin/getconf, not PATH's: a compat-layer userland (e.g. EESSI on /cvmfs)
# shadows getconf and reports ITS glibc rather than the host's, which would compare
# the image against the wrong reference and suppress a real overshoot warning.
_GC=/usr/bin/getconf; [ -x "$_GC" ] || _GC="$(command -v getconf 2>/dev/null || true)"
if [ -n "$_GC" ]; then
  _hg="$("$_GC" GNU_LIBC_VERSION 2>/dev/null)"
  _sg="$("$APPTAINER" exec "$OUT" "$_GC" GNU_LIBC_VERSION 2>/dev/null)"
  echo "   glibc: image='${_sg:-?}'  build-host='${_hg:-?}'  (image must be <= your compute nodes')"
  if bash "$REPO_ROOT/install/sif/glibc-floor.sh" "$_sg" "$_hg"; then
    cat >&2 <<EOF

  ##########################  glibc MISMATCH — READ THIS  ##########################
  # The SIF base glibc (${_sg}) is NEWER than this host's (${_hg}).
  #   → tools compiled INSIDE the container will fail on the compute nodes, and
  #     host environment-modules (Lmod) will NOT load inside the container.
  # Rebuild on a base whose glibc <= your nodes' (match the cluster OS family), e.g.:
  #     ABA_SIF_BASE=oraclelinux:7 $0 --profile ${PROFILE}      # or  --base <el-image>
  ##################################################################################
EOF
  fi
fi
