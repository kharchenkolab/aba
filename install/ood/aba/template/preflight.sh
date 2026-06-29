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
binds=(--bind "${staged}:${staged}" --bind /cluster/aba:/cluster/aba)
[ -d /groups ] && binds+=(--bind /groups:/groups)
[ -n "${ABA_PF_HOME:-}" ] && [ -d "${ABA_PF_HOME}" ] && binds+=(--bind "${ABA_PF_HOME}:${ABA_PF_HOME}")

# apptainer scrubs most host env → pass the preflight inputs explicitly.
envs=(--env "ABA_SITE_CONFIG=${ABA_SITE_CONFIG}"
      --env "ABA_PF_GROUP=${ABA_PF_GROUP:-}"
      --env "ABA_PF_USER=${ABA_PF_USER:-}"
      --env "ABA_PF_HOME=${ABA_PF_HOME:-}"
      --env "ABA_PF_STAGED=${staged}")
[ -n "${ABA_PF_TOKEN:-}" ] && envs+=(--env "ABA_PF_TOKEN=${ABA_PF_TOKEN}")

echo "preflight.sh: apptainer exec ${SIF} python /opt/aba/ood/aba_preflight.py" >> "$log"
apptainer exec "${binds[@]}" "${envs[@]}" "${SIF}" \
  /opt/aba-venv/bin/python /opt/aba/ood/aba_preflight.py >> "$log" 2>&1
exit $?
