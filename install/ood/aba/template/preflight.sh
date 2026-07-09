#!/usr/bin/env bash
# Run aba-preflight FROM the SIF (baked at /opt/aba/ood/aba_preflight.py) — so it
# works on any node, with the image's python, version-locked to the backend (no
# dev paths). before.sh.erb sets ABA_SITE_CONFIG + ABA_PF_* in the env, then runs
# this. The in-image preflight writes aba-env.sh + status.yaml to ABA_PF_STAGED;
# we return its exit code (10 = blocked → before.sh aborts the launch).
set -u
staged="${ABA_PF_STAGED:-$PWD}"
log="${staged}/preflight.log"

# The one value we must read from site.yaml *before* preflight runs: the image
# path (preflight emits everything else). Dependency-free parse (no ruby/pyyaml
# on the node): the `sif:` key under `image:`.
SIF="$(grep -E '^[[:space:]]*sif:' "${ABA_SITE_CONFIG}" 2>/dev/null \
        | head -1 | sed -E 's/^[^:]*:[[:space:]]*//; s/[[:space:]"]//g' | tr -d "'")"
if [ -z "${SIF}" ] || [ ! -e "${SIF}" ]; then
  echo "preflight.sh: could not resolve image.sif from ${ABA_SITE_CONFIG} (got '${SIF}')" >> "$log"
  exit 1
fi

# Binds: the staged dir (preflight writes aba-env.sh/status.yaml there), the site
# config root, and — when present — the lab shares + the user's home.
binds=(--bind "${staged}:${staged}")
[ -d /cluster/aba ] && binds+=(--bind /cluster/aba:/cluster/aba)
[ -d /groups ] && binds+=(--bind /groups:/groups)
[ -n "${ABA_PF_HOME:-}" ] && [ -d "${ABA_PF_HOME}" ] && binds+=(--bind "${ABA_PF_HOME}:${ABA_PF_HOME}")

# SLIM image: the conda venv (with preflight's own python + PyYAML) is NOT baked
# into the image — it lives in the shared base, mounted at /opt/aba-venv only at
# RUN time (script.sh.erb). Preflight runs BEFORE that, so we must resolve + bind
# the base here too, or `/opt/aba-venv/bin/python` below doesn't exist and the
# whole launch silently loses its resolved env. Resolve the base the same
# dependency-free way the run path does: image.release_root/current wins (versioned
# deploy), else the static image.base_dir. Expand {group}/{user}/{home}. Fat images
# bake the venv (no base_dir) → BASE stays empty → no bind (unchanged).
_pf_expand() {   # substitute {group}/{user}/{home} — mirrors aba_preflight.expand()
  printf '%s' "$1" | sed -e "s#{group}#${ABA_PF_GROUP:-}#g" \
                         -e "s#{user}#${ABA_PF_USER:-}#g" \
                         -e "s#{home}#${ABA_PF_HOME:-}#g"
}
_pf_yaml1() {    # first scalar value of key $1 (same parse as the sif: grep above)
  grep -E "^[[:space:]]*$1:" "${ABA_SITE_CONFIG}" 2>/dev/null \
    | head -1 | sed -E 's/^[^:]*:[[:space:]]*//; s/[[:space:]"]//g' | tr -d "'"
}
BASE=""
RELROOT="$(_pf_yaml1 release_root)"
if [ -n "${RELROOT}" ]; then
  cur="$(_pf_expand "${RELROOT}")/current"
  [ -L "${cur}" ] && BASE="$(readlink -f "${cur}")/env/aba-venv"
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
_ng="$(getconf GNU_LIBC_VERSION 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
_sg="$(apptainer exec "${SIF}" getconf GNU_LIBC_VERSION 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
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
[ -n "${ABA_PF_TOKEN:-}" ] && envs+=(--env "ABA_PF_TOKEN=${ABA_PF_TOKEN}")
[ -n "${GLIBC_WARN}" ] && envs+=(--env "ABA_PF_GLIBC_WARN=${GLIBC_WARN}")

echo "preflight.sh: apptainer exec ${SIF} python /opt/aba/ood/aba_preflight.py" >> "$log"
apptainer exec "${binds[@]}" "${envs[@]}" "${SIF}" \
  /opt/aba-venv/bin/python /opt/aba/ood/aba_preflight.py >> "$log" 2>&1
exit $?
