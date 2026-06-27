#!/usr/bin/env bash
# ABA — Linux installer bootstrap (install type 2). Run from a cloned checkout:
#   git clone git@github.com:kharchenkolab/aba.git
#   cd aba && ./install/linux/setup.sh                       # desktop: helper UI + browser
#   ./install/linux/setup.sh --headless                      # server / login node: no browser
#   ./install/linux/setup.sh --install-dir /opt/aba          # put the WHOLE install elsewhere (default ~/.aba)
#   ./install/linux/setup.sh --runtime-dir /data/$USER/aba   # just projects/data on a bigger disk
#   ./install/linux/setup.sh --cluster-personal --runtime-dir /shared/$USER/aba   # Slurm offload
#
# The aba repo is private for now (ssh+key); this installs FROM the checkout it
# lives in (ABA_REPO_SRC=self), so no second clone/auth is needed. The public
# recipe pack is cloned over https. Overrides: ABA_REPO_URL / ABA_RECIPES_URL
# (alternate git URLs), ABA_REPO_SRC / ABA_RECIPES_SRC (local paths), ABA_HOME.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYBOOT="${ABA_PYTHON:-python3}"   # python used to make the helper venv (override if the system one lacks venv)

HEADLESS=0; PROFILE="local"; RUNTIME_DIR=""; INSTALL_DIR=""; API_KEY=""; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --headless)                  HEADLESS=1 ;;
    --cluster-personal|--slurm)  PROFILE="cluster-personal"; HEADLESS=1 ;;
    --install-dir)               INSTALL_DIR="${2:?--install-dir needs a path}"; shift ;;
    --runtime-dir)               RUNTIME_DIR="${2:?--runtime-dir needs a path}"; shift ;;
    --api-key)                   API_KEY="${2:?--api-key needs a value}"; shift ;;
    -h|--help)                   sed -n '2,14p' "$0"; exit 0 ;;
    *)                           EXTRA+=("$1") ;;
  esac; shift
done

# The WHOLE install (env, helper, launcher, recipes) lives under ABA_HOME.
# Precedence: --install-dir, then an exported ABA_HOME, then ~/.aba.
export ABA_HOME="${INSTALL_DIR:-${ABA_HOME:-$HOME/.aba}}"
HELPER="$ABA_HOME/installer"     # helper venv + state (same path as the Mac installer, for a uniform launcher)

echo "== ABA Linux installer =="
echo "   repo:    $REPO_ROOT"
echo "   home:    $ABA_HOME"
echo "   profile: $PROFILE (headless=$HEADLESS)"

# --- prerequisites (classical, actionable; the no-agent path) ---
miss=""
for t in git curl; do command -v "$t" >/dev/null 2>&1 || miss="$miss $t"; done
command -v "$PYBOOT" >/dev/null 2>&1 || miss="$miss python3"
# venv needs ensurepip (the python3-venv package on Debian/Ubuntu); test it.
"$PYBOOT" -c 'import ensurepip' 2>/dev/null || miss="$miss python3-venv"
if [ -n "$miss" ]; then
  echo "MISSING:$miss"
  echo "  Install the package(s) above, then re-run:"
  echo "    Debian/Ubuntu: sudo apt install git curl python3-venv"
  echo "    Fedora/RHEL:   sudo dnf install git curl python3"
  echo "  (or set ABA_PYTHON to a python3 that already has venv+pip, e.g. a conda python)"
  exit 1
fi

# --- the (private) aba repo: install from THIS checkout, not a re-clone ---
export ABA_REPO_SRC="${ABA_REPO_SRC:-$REPO_ROOT}"
echo "   aba source: local checkout ($ABA_REPO_SRC)"

# --- installer helper venv (runs the shared playbook) ---
mkdir -p "$ABA_HOME"
if [ ! -x "$HELPER/venv/bin/python" ]; then
  echo "-- creating installer helper venv ($PYBOOT) --"
  "$PYBOOT" -m venv "$HELPER/venv"
  "$HELPER/venv/bin/pip" install -q --upgrade pip >/dev/null
fi
"$HELPER/venv/bin/pip" install -q "$REPO_ROOT/install/core/helper" >/dev/null
PY="$HELPER/venv/bin/python"

# --- credential (optional, simple path; OAuth paste-flow lands later) ---
write_cfg() { CFG="$ABA_HOME/config.env"; touch "$CFG"; chmod 600 "$CFG"; grep -q "^$1=" "$CFG" 2>/dev/null && sed -i "s|^$1=.*|$1=$2|" "$CFG" || echo "$1=$2" >> "$CFG"; }
if [ -n "$API_KEY" ]; then
  write_cfg ANTHROPIC_API_KEY "$API_KEY"
  write_cfg ABA_LLM_CREDENTIAL apikey
  echo "   credential: API key written to config.env"
fi

# --- runtime location (optional; default $ABA_HOME/runtime) ---
# Holds projects, data, results, and the per-user envs — the part that grows.
# Point it at a bigger/faster disk while the base env stays under $ABA_HOME.
# Applies to ANY profile; cluster-personal additionally REQUIRES it (and it must
# be on the shared filesystem). Persisted to config.env so the launcher uses it.
if [ -n "$RUNTIME_DIR" ]; then
  mkdir -p "$RUNTIME_DIR"; export ABA_RUNTIME_DIR="$RUNTIME_DIR"
  write_cfg ABA_RUNTIME_DIR "$RUNTIME_DIR"
  echo "   runtime: $RUNTIME_DIR"
fi

# --- cluster-personal profile: Slurm offload + shared-FS runtime + hpc.yaml ---
if [ "$PROFILE" = "cluster-personal" ]; then
  if [ -z "$RUNTIME_DIR" ]; then
    echo "ERROR: --cluster-personal needs --runtime-dir DIR on the SHARED filesystem"
    echo "       (Slurm compute nodes must be able to read the project/runtime files)."
    exit 1
  fi
  write_cfg ABA_BATCH_SUBMITTER slurm
  echo "-- cluster-personal: ABA_BATCH_SUBMITTER=slurm, runtime=$RUNTIME_DIR --"
  if command -v sinfo >/dev/null 2>&1; then
    "$PY" -m aba_installer.cli hpc-config --out "$ABA_HOME/hpc.yaml" || true
    [ -f "$ABA_HOME/hpc.yaml" ] && write_cfg ABA_HPC_CONFIG "$ABA_HOME/hpc.yaml"
  else
    echo "   (no sinfo here — run on a submit-capable node, or write $ABA_HOME/hpc.yaml"
    echo "    by hand; the runtime router also queries the scheduler live)"
  fi
fi

# --- run the install ---
if [ "$HEADLESS" = 1 ]; then
  echo "-- running install (headless) --"
  exec "$PY" -m aba_installer.cli install ${EXTRA[@]+"${EXTRA[@]}"}
else
  echo "-- starting installer helper (browser UI) --"
  ABA_HOME="$ABA_HOME" nohup "$HELPER/venv/bin/aba-installer" >"$HELPER/helper.log" 2>&1 &
  sleep 2
  PORT="$(cat "$ABA_HOME/installer/port.txt" 2>/dev/null || echo 8765)"
  URL="http://127.0.0.1:$PORT"
  echo "   helper UI: $URL"
  command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" >/dev/null 2>&1 || echo "   open $URL in a browser to continue."
fi
