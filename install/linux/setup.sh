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
PYBOOT=""                         # bootstrap python — resolved below (ABA_PYTHON / PATH / modules / micromamba)
MIN_PY_MINOR=9                    # helper needs Python >=3.9 (pyproject requires-python) + setuptools>=68

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

# --- prerequisites: git + curl (the rest — a modern Python — we stand up) ---
miss=""
for t in git curl; do command -v "$t" >/dev/null 2>&1 || miss="$miss $t"; done
if [ -n "$miss" ]; then
  echo "MISSING:$miss"
  echo "  Install the tool(s) above, then re-run:"
  echo "    Debian/Ubuntu: sudo apt install$miss"
  echo "    Fedora/RHEL:   sudo dnf install$miss"
  exit 1
fi

# --- bootstrap Python (sets PYBOOT) -----------------------------------------
# The helper (install/core/helper) needs Python >=3.9 + setuptools>=68. A bare
# RHEL7 python3 is 3.6: it PASSES a naive "import ensurepip" check yet then dies
# deep in pip ("No matching distribution found for setuptools>=68"). So we check
# the *version*, and rather than fail we try, in order: the newest interpreter
# on PATH, the newest Python environment-module (HPC), then a private Python we
# stand up via micromamba. Override the whole dance with ABA_PYTHON=/path/python3.
py_ver()    { "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null; }
py_usable() { "$1" -c 'import sys,ensurepip; sys.exit(0 if sys.version_info[:2]>=(3,'"$MIN_PY_MINOR"') else 1)' >/dev/null 2>&1; }

# (modules) newest python module >=3.9 on an Lmod / environment-modules system.
try_module_python() {
  if ! command -v module >/dev/null 2>&1; then          # define `module` in this non-login shell
    for f in "${LMOD_PKG:-}/init/bash" "${MODULESHOME:-}/init/bash" \
             /etc/profile.d/lmod.sh /etc/profile.d/z00_lmod.sh /etc/profile.d/modules.sh; do
      [ -r "$f" ] && . "$f" >/dev/null 2>&1 && command -v module >/dev/null 2>&1 && break
    done
  fi
  command -v module >/dev/null 2>&1 || return 1
  echo "   no modern Python on PATH — searching environment modules…"
  local best
  best="$(module -t avail python 2>&1 | awk -v min="$MIN_PY_MINOR" '
    /^python\/[0-9]/ { n=$0; sub(/\(.*\)/,"",n); gsub(/[ \t]+$/,"",n);
      v=n; sub(/^python\//,"",v); split(v,a,/[.-]/);
      if (a[1]==3 && a[2]>=min) { k=a[2]*1000+(a[3]+0); if (n ~ /-bare/) k--;   # prefer full over -bare
        if (k>bk){bk=k; bn=n} } }
    END { if (bn!="") print bn }')"
  [ -n "$best" ] || { echo "   (no python module >=3.$MIN_PY_MINOR available)"; return 1; }
  echo "   loading module: $best"
  module load "$best" >/dev/null 2>&1 || { echo "   (module load $best failed)"; return 1; }
  local c p
  for c in python3 python; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    py_usable "$p" || continue
    PYBOOT="$p"
    echo "   NOTE: used 'module load $best' for this install. If a later 'aba update' /"
    echo "         'aba doctor' errors on libpython, load that module first, or set ABA_PYTHON."
    return 0
  done
  return 1
}

# (micromamba) last resort: a self-contained Python that needs no system python.
# Reuses $ABA_HOME/bin/micromamba, so the later install-micromamba step is a no-op.
bootstrap_python_via_micromamba() {
  echo "   bootstrapping a private Python via micromamba…"
  local mm="$ABA_HOME/bin/micromamba" bp="$ABA_HOME/bootstrap" plat
  mkdir -p "$ABA_HOME/bin"
  if [ ! -x "$mm" ]; then
    plat="${ABA_MAMBA_PLATFORM:-$(case "$(uname -s)/$(uname -m)" in (Linux/aarch64|Linux/arm64) echo linux-aarch64;; (Linux/*) echo linux-64;; (Darwin/*) echo "osx-$(uname -m)";; (*) echo linux-64;; esac)}"
    curl -fsSL "https://micro.mamba.pm/api/micromamba/$plat/latest" | tar -xj -C "$ABA_HOME" bin/micromamba || return 1
  fi
  { [ -x "$bp/bin/python" ] || "$mm" create -y -q -p "$bp" -c conda-forge "python>=3.$MIN_PY_MINOR" pip >/dev/null 2>&1; } || return 1
  py_usable "$bp/bin/python" || return 1
  PYBOOT="$bp/bin/python"
  return 0
}

if [ -n "${ABA_PYTHON:-}" ]; then                        # explicit override wins — but is validated
  if py_usable "$ABA_PYTHON"; then PYBOOT="$ABA_PYTHON"
  else echo "ERROR: ABA_PYTHON=$ABA_PYTHON is unusable (need Python >=3.$MIN_PY_MINOR with pip; got $(py_ver "$ABA_PYTHON" || echo none))."; exit 1; fi
else
  for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3 python; do
    p="$(command -v "$c" 2>/dev/null)" || continue
    if py_usable "$p"; then PYBOOT="$p"; break; fi
  done
  [ -n "$PYBOOT" ] || try_module_python            || true
  [ -n "$PYBOOT" ] || bootstrap_python_via_micromamba || true
fi
if [ -z "$PYBOOT" ]; then
  echo "ERROR: no usable Python (>=3.$MIN_PY_MINOR) found, and bootstrap failed."
  echo "  Install Python >=3.$MIN_PY_MINOR, load a python module, or set ABA_PYTHON=/path/to/python3, then re-run."
  exit 1
fi
echo "   python:  $PYBOOT ($(py_ver "$PYBOOT"))"

# Test seam: resolve the bootstrap python, then stop — so the resolver can be
# exercised in isolation without the full install (see tests/test_setup_sh_python.py).
[ -n "${ABA_RESOLVE_PYTHON_ONLY:-}" ] && { echo "RESOLVED_PYBOOT=$PYBOOT"; exit 0; }

# --- the (private) aba repo: install from THIS checkout, not a re-clone ---
export ABA_REPO_SRC="${ABA_REPO_SRC:-$REPO_ROOT}"
echo "   aba source: local checkout ($ABA_REPO_SRC)"
# create-env runs BEFORE clone-repos, so $REPO_DIR/aba isn't populated yet; with
# a private/local install the playbook would otherwise fetch environment.yml from
# a GitHub raw URL that 404s. We're installing from a checkout — hand both conda
# specs to the playbook directly (the r-env step is post-clone, but set it too
# for consistency and fully-offline installs).
export ABA_ENV_YML_SRC="${ABA_ENV_YML_SRC:-$REPO_ROOT/install/core/environment.yml}"
export ABA_R_ENV_YML_SRC="${ABA_R_ENV_YML_SRC:-$REPO_ROOT/install/core/r-environment.yml}"

# --- installer helper venv (runs the shared playbook) ---
mkdir -p "$ABA_HOME"
# Rebuild when missing OR when the existing venv's python is too old (e.g. a
# venv from an earlier run under a stale system python3) — never reuse a bad one.
if [ ! -x "$HELPER/venv/bin/python" ] || ! py_usable "$HELPER/venv/bin/python"; then
  echo "-- creating installer helper venv ($PYBOOT, $(py_ver "$PYBOOT")) --"
  rm -rf "$HELPER/venv"
  "$PYBOOT" -m venv "$HELPER/venv"
  "$HELPER/venv/bin/python" -m pip install -q --upgrade pip setuptools wheel >/dev/null
fi
"$HELPER/venv/bin/python" -m pip install -q "$REPO_ROOT/install/core/helper" >/dev/null
PY="$HELPER/venv/bin/python"

# --- credential (optional, simple path; OAuth paste-flow lands later) ---
write_cfg() { CFG="$ABA_HOME/config.env"; touch "$CFG"; chmod 600 "$CFG"; grep -q "^$1=" "$CFG" 2>/dev/null && sed -i "s|^$1=.*|$1=$2|" "$CFG" || echo "$1=$2" >> "$CFG"; }

# Keep micromamba's package cache on the SAME filesystem as the install, so it
# HARDLINKS packages into each env prefix instead of copying them file-by-file.
# Default cache (~/.local/share/mamba) lives under $HOME; on a cluster $HOME and
# the install dir are often different NFS mounts, and a cross-mount install copies
# tens of thousands of small files — minutes instead of seconds. ABA_HOME and the
# runtime usually share a filesystem, so one cache here serves both env builds.
# Persisted to config.env so `aba update` and on-demand env builds benefit too.
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$ABA_HOME}"
export CONDA_PKGS_DIRS="${CONDA_PKGS_DIRS:-$ABA_HOME/pkgs}"
# pip's wheel cache defaults to ~/.cache/pip ($HOME) too — pin it under the install
# so nothing leaks outside ABA_HOME (keeps uninstall = one `rm -rf $ABA_HOME`).
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$ABA_HOME/pip-cache}"
write_cfg MAMBA_ROOT_PREFIX "$MAMBA_ROOT_PREFIX"
write_cfg CONDA_PKGS_DIRS "$CONDA_PKGS_DIRS"
write_cfg PIP_CACHE_DIR "$PIP_CACHE_DIR"
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
