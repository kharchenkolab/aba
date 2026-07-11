#!/usr/bin/env bash
# Install the lstar R viewer bridge into a conda R "tools" env.
#
# The pagoda3 viewer opens Seurat / SingleCellExperiment `.rds` results by bridging
# through the tools env's Rscript, which needs the lstar R package. It is NOT on CRAN
# (lstar moves fast); we pin the git tag below — the SAME release moment as the Python
# `lstar-sc` (same repo, R package under `R/`) and the pagoda3 dist, so writer/reader/
# store-format stay a matched set. BUMP THESE PINS TOGETHER.
#
# Single source of truth for the lstar-R install, shared by every target that provisions
# a tools env: the install/update playbooks (install.yml / update.yml — native
# linux/cluster/mac) AND the SIF builders (install/sif/build.sh fat + aba-vbc slim base).
# Before this helper the logic was copy-pasted; it drifted once (pagoda3 sibling stayed
# install-only → dark on update), so it lives here now. Mirrors inject-accelerator.sh.
#
# Why NOT remotes::install_github: it resolves the repo + subdir through the GitHub API,
# which is CASE-SENSITIVE on `subdir` — the package lives under `R/` (capital), and a
# lowercase `r` 404s the Contents API (surfaced as a misleading "did you spell the repo
# owner correctly?" — this is what silently broke .rds viewing). Fetching the tag tarball
# from codeload sidesteps the API entirely (find the pkg dir ourselves, case and all) and
# is also not bound by the API's 60-req/hr unauth budget shared across the cluster NAT IP.
# Same mechanism as the pagoda3 dist. The compile runs INSIDE the activated env (`micromamba run`) so
# the conda toolchain supplies zlib.h + the right flags, then we rewrite the built .so's
# rpath to $ORIGIN-relative (conda's own convention) so a RELOCATED env — the SIF stages
# the tools env then moves it to /opt/aba-envs/tools — still resolves its libs at runtime.
#
# NON-FATAL by contract: on any failure it warns and exits 0 — without lstar-R the viewer
# still opens `.h5ad` / `.lstar.zarr`; only Seurat/SCE `.rds` conversion degrades.
#
# Usage: install-lstar-r.sh <tools_env_dir> [micromamba_bin]   (idempotent; skips at pin)
set -uo pipefail   # NOT -e: non-fatal by design (see contract above)

LSTAR_REF="v0.2.1"   # bump together with lstar-sc (pip) + the pagoda3 dist

TOOLS_ENV="${1:?usage: install-lstar-r.sh <tools_env_dir> [micromamba_bin]}"
# micromamba: explicit arg wins, then $ABA_MICROMAMBA, then PATH. Needed to `run` inside
# the activated env so the conda compiler's include/lib/rpath flags are set.
MM="${2:-${ABA_MICROMAMBA:-$(command -v micromamba 2>/dev/null || true)}}"

RS="$TOOLS_ENV/bin/Rscript"
LIBDIR="$TOOLS_ENV/lib/R/library"
MARKER="$LIBDIR/lstar/ABA_INSTALLED_REF"   # our idempotency stamp (no RemoteRef here)
URL="${ABA_LSTAR_TARBALL_URL:-https://github.com/kharchenkolab/lstar/archive/refs/tags/$LSTAR_REF.tar.gz}"

if [ ! -x "$RS" ]; then
  echo "no R tools env at $TOOLS_ENV — skipping lstar R"
  exit 0
fi
if [ -f "$MARKER" ] && grep -qs "^$LSTAR_REF$" "$MARKER"; then
  echo "lstar R $LSTAR_REF already installed — skipping"
  exit 0
fi
if [ ! -x "$MM" ]; then
  echo "WARNING: no micromamba (pass as \$2 or set ABA_MICROMAMBA) — cannot compile lstar R; .rds viewer bridge unavailable (.h5ad/.lstar.zarr still work)"
  exit 0
fi

echo "[lstar-r] installing kharchenkolab/lstar@$LSTAR_REF into $TOOLS_ENV"

# ensure the compile prerequisites are in the env: zlib (the .so needs zlib.h — the env
# otherwise only has runtime libzlib) + patchelf (rpath rewrite below). r-environment.yml
# declares both for FRESH builds, but `aba update` does NOT refresh the R tools env, so a
# pre-existing env may lack them — install them here (idempotent/fast when already present,
# only reached on an actual (re)install since the marker check short-circuits above).
"$MM" install -y -p "$TOOLS_ENV" zlib patchelf >/dev/null 2>&1 \
  || echo "NOTE: could not ensure zlib/patchelf in the tools env — compile may fail if zlib.h is absent"

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# 1. fetch the tag tarball from codeload (no GitHub API → no rate-limit / 404)
if ! curl -fsSL --max-time 120 "$URL" -o "$TMP/lstar.tar.gz"; then
  echo "WARNING: lstar tarball fetch failed ($URL) — .rds viewer bridge unavailable (.h5ad/.lstar.zarr still work)"
  exit 0
fi
tar xzf "$TMP/lstar.tar.gz" -C "$TMP" || { echo "WARNING: lstar tarball unpack failed — .rds bridge unavailable"; exit 0; }

# 2. locate the R package dir (the DESCRIPTION with Package: lstar) — case-robust, so a
#    repo that houses the package under R/ (capital) vs r/ doesn't matter.
DESC="$(find "$TMP" -name DESCRIPTION -exec grep -l '^Package: lstar' {} + 2>/dev/null | head -1)"
if [ -z "$DESC" ]; then
  echo "WARNING: no lstar R package (DESCRIPTION) in the tarball — .rds bridge unavailable"
  exit 0
fi
PKG="$(dirname "$DESC")"

# 3. compile INSIDE the activated env so the conda toolchain's zlib.h + CPPFLAGS/rpath
#    are in effect (a bare PATH prepend does not run the compiler-activation scripts).
if ! "$MM" run -p "$TOOLS_ENV" R CMD INSTALL --no-multiarch "$PKG"; then
  echo "WARNING: lstar R compile failed — Seurat/SCE .rds viewer bridge unavailable (.h5ad/.lstar.zarr still work)"
  exit 0
fi

# 4. make the built .so relocatable: rewrite its absolute rpath (the build-time env path)
#    to $ORIGIN-relative, matching conda's packages — so the SIF's moved env still finds
#    its libs. From <env>/lib/R/library/lstar/libs, four levels up is <env>/lib.
SO="$(find "$LIBDIR/lstar/libs" -name '*.so' 2>/dev/null | head -1)"
if [ -n "$SO" ] && [ -x "$TOOLS_ENV/bin/patchelf" ]; then
  "$TOOLS_ENV/bin/patchelf" --set-rpath '$ORIGIN/../../../..' "$SO" \
    && echo "[lstar-r] rpath set \$ORIGIN-relative (relocation-safe)" \
    || echo "NOTE: patchelf rpath rewrite failed — ok for an in-place env, but a relocated (SIF) env may not resolve libs"
elif [ -n "$SO" ]; then
  echo "NOTE: no patchelf in the tools env — rpath left absolute (fine in place; a relocated SIF env may not resolve libs)"
fi

echo "$LSTAR_REF" > "$MARKER"
echo "[lstar-r] installed lstar R $LSTAR_REF"
exit 0
