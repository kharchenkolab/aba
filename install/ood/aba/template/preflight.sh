#!/usr/bin/env bash
# Run aba-preflight for this launch. It writes aba-env.sh (the env block) +
# status.yaml (the session card) to ABA_PF_STAGED and returns an exit code
# (10 = blocked → before.sh aborts the launch).
#
# Three lanes, tried in order:
#   1. image.sif set in site.yaml         → run preflight INSIDE that SIF.
#   2. image.release_root set (weft/slim) → resolve <root>/current/sif/*.{sif,sqfs}
#                                           and run preflight INSIDE that release SIF.
#   3. neither (bare dev harness)         → run aba_preflight.py with a host python
#                                           (release-payload copy; needs PyYAML).
# Lanes 1 & 2 use the image's own python (PyYAML guaranteed, version-locked). Only a
# share with no image AND no release SIF falls to lane 3.
set -u
staged="${ABA_PF_STAGED:-$PWD}"
log="${staged}/preflight.log"

# Dependency-free site.yaml reads (no ruby/pyyaml on the node before preflight runs).
_pf_expand() {   # substitute {group}/{user}/{home} — mirrors aba_preflight.expand()
  printf '%s' "$1" | sed -e "s#{group}#${ABA_PF_GROUP:-}#g" \
                         -e "s#{user}#${ABA_PF_USER:-}#g" \
                         -e "s#{home}#${ABA_PF_HOME:-}#g"
}
_pf_yaml1() {    # first scalar value of key $1 (any parent: image:/envs:/...)
  # strip the key, then a YAML inline comment (' #…'), then quotes/space. The comment
  # cut MUST precede whitespace removal, or a commented path (release_root: /p  # note)
  # gloms the comment onto the value. PyYAML (inside the SIF) does this right; this
  # dependency-free reader must match it.
  grep -E "^[[:space:]]*$1:" "${ABA_SITE_CONFIG}" 2>/dev/null \
    | head -1 | sed -E 's/^[^:]*:[[:space:]]*//; s/[[:space:]]+#.*$//; s/[[:space:]"]//g' | tr -d "'"
}
# The share root holds site.yaml (+ image/release, skeleton, bundle). Derive it from
# ABA_SITE_CONFIG rather than assuming /cluster/aba, so a site can root the deployment
# anywhere its nodes can read (incl. a home dir, for a pilot).
_share="${ABA_SHARE:-$(dirname "${ABA_SITE_CONFIG}")}"

# <release_root>/current pin: the release ABA_RELEASE_ID names (if it exists), else the
# live `current` symlink. Used by both the SIF and BASE resolution below.
_pf_release_dir() {   # $1 = release_root (already expanded)
  local rr="$1" cur
  cur="${ABA_RELEASE_ID:+${rr}/releases/${ABA_RELEASE_ID}}"
  { [ -z "${cur}" ] || [ ! -e "${cur}" ]; } && cur="${rr}/current"
  printf '%s' "${cur}"
}

# ── resolve the image (lane 1: image.sif; lane 2: the promoted release's SIF) ──
SIF="$(_pf_yaml1 sif)"
RELROOT="$(_pf_yaml1 release_root)"
if { [ -z "${SIF}" ] || [ ! -e "${SIF}" ]; } && [ -n "${RELROOT}" ]; then
  _cur="$(_pf_release_dir "$(_pf_expand "${RELROOT}")")"
  SIF="$(ls "${_cur}"/sif/*.sif "${_cur}"/sif/*.sqfs 2>/dev/null | head -1)"
fi

if [ -z "${SIF}" ] || [ ! -e "${SIF}" ]; then
  # ── lane 3: no image at all (bare dev harness) — run aba_preflight.py DIRECTLY with
  # a host python. Version-locking still holds when the share carries a promoted
  # release: the payload brings its own copy of the script. Interpreter (needs PyYAML):
  # explicit ABA_PF_PYTHON, else site.yaml venv:, else python3.
  _rel="${ABA_RELEASE_ID:+${_share}/releases/${ABA_RELEASE_ID}}"
  [ -n "${_rel}" ] && [ ! -e "${_rel}" ] && _rel=""
  _rel="${_rel:-${_share}/current}"
  PF=""
  [ -e "${_rel}/repo/install/ood/aba_preflight.py" ] \
    && PF="$(readlink -f "${_rel}/repo")/install/ood/aba_preflight.py"
  [ -z "${PF}" ] && [ -n "${ABA_PF_SCRIPT:-}" ] && [ -e "${ABA_PF_SCRIPT}" ] \
    && PF="${ABA_PF_SCRIPT}"
  if [ -z "${PF}" ]; then
    echo "preflight.sh: no image.sif and no release SIF in ${ABA_SITE_CONFIG}, and no release preflight at ${_rel}/repo/install/ood/aba_preflight.py" >> "$log"
    exit 1
  fi
  PY="${ABA_PF_PYTHON:-}"
  if [ -z "${PY}" ]; then
    _venv="$(_pf_yaml1 venv)"
    [ -n "${_venv}" ] && [ -x "${_venv}/bin/python" ] && PY="${_venv}/bin/python"
  fi
  PY="${PY:-python3}"
  echo "preflight.sh: no-SIF (host-python) lane: ${PY} ${PF}" >> "$log"
  "${PY}" "${PF}" >> "$log" 2>&1
  exit $?
fi
echo "preflight.sh: image lane: ${SIF}" >> "$log"

# ── lanes 1 & 2: run preflight INSIDE the image ──
# Binds: the staged dir (preflight writes aba-env.sh/status.yaml there), the site
# config root, and — when present — the lab shares + the user's home.
binds=(--bind "${staged}:${staged}")
[ -d "${_share}" ] && binds+=(--bind "${_share}:${_share}")
[ -d /groups ] && binds+=(--bind /groups:/groups)
[ -n "${ABA_PF_HOME:-}" ] && [ -d "${ABA_PF_HOME}" ] && binds+=(--bind "${ABA_PF_HOME}:${ABA_PF_HOME}")

# SLIM image: the conda venv (with preflight's own python + PyYAML) is NOT baked into
# the image — it lives in the shared base, mounted at /opt/aba-venv only at RUN time.
# Preflight runs BEFORE that, so resolve + bind the base here too. image.release_root/
# current/env/aba-venv wins (versioned deploy), else static image.base_dir. A FAT or
# WEFT image bakes its own controller venv (no env component) → BASE stays empty → no
# bind (the baked /opt/aba-venv is used, unchanged).
BASE=""
if [ -n "${RELROOT}" ]; then
  _cur="$(_pf_release_dir "$(_pf_expand "${RELROOT}")")"
  [ -x "${_cur}/env/aba-venv/bin/python" ] && BASE="${_cur}/env/aba-venv"
else
  BD="$(_pf_yaml1 base_dir)"
  [ -n "${BD}" ] && BASE="$(_pf_expand "${BD}")"
fi
if [ -n "${BASE}" ] && [ -d "${BASE}" ]; then
  binds+=(--bind "${BASE}:/opt/aba-venv")
  echo "preflight.sh: slim base bound ${BASE} -> /opt/aba-venv" >> "$log"
fi

# glibc-floor check (mirrors install/sif/glibc-floor.sh): the image's base glibc must
# be <= THIS node's, or in-container-compiled tools + host environment-modules break
# here. Non-fatal — surfaced on the session card via ABA_PF_GLIBC_WARN. Catches a
# mis-based image at launch even when it was built elsewhere.
GLIBC_WARN=""
# Use /usr/bin/getconf, not PATH's: a compat-layer userland (e.g. EESSI on /cvmfs)
# shadows getconf and reports ITS glibc, not the node's — comparing against that
# silently defeats the check.
_gc=/usr/bin/getconf; [ -x "$_gc" ] || _gc=getconf
_ng="$("$_gc" GNU_LIBC_VERSION 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
_sg="$(apptainer exec "${SIF}" "$_gc" GNU_LIBC_VERSION 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
if [ -n "${_ng}" ] && [ -n "${_sg}" ] && [ "${_ng}" != "${_sg}" ] \
   && [ "$(printf '%s\n%s\n' "${_sg}" "${_ng}" | sort -t. -k1,1n -k2,2n | tail -1)" = "${_sg}" ]; then
  GLIBC_WARN="SIF base glibc ${_sg} exceeds this node's ${_ng} — tools compiled in-container and host modules (Lmod) will fail on the compute nodes; rebuild the image on a base with glibc <= ${_ng} (ABA_SIF_BASE)."
  echo "preflight.sh: WARNING ${GLIBC_WARN}" >> "$log"
fi

# apptainer scrubs most host env → pass the preflight inputs explicitly.
envs=(--env "ABA_SITE_CONFIG=${ABA_SITE_CONFIG}"
      --env "ABA_PF_GROUP=${ABA_PF_GROUP:-}"
      --env "ABA_PF_USER=${ABA_PF_USER:-}"
      --env "ABA_PF_HOME=${ABA_PF_HOME:-}"
      --env "ABA_PF_STAGED=${staged}")
[ -n "${ABA_RELEASE_ID:-}" ] && envs+=(--env "ABA_RELEASE_ID=${ABA_RELEASE_ID}")
[ -n "${ABA_PF_TOKEN:-}" ] && envs+=(--env "ABA_PF_TOKEN=${ABA_PF_TOKEN}")
[ -n "${GLIBC_WARN}" ] && envs+=(--env "ABA_PF_GLIBC_WARN=${GLIBC_WARN}")

echo "preflight.sh: apptainer exec ${SIF} python /opt/aba/ood/aba_preflight.py" >> "$log"
apptainer exec "${binds[@]}" "${envs[@]}" "${SIF}" \
  /opt/aba-venv/bin/python /opt/aba/ood/aba_preflight.py >> "$log" 2>&1
exit $?
