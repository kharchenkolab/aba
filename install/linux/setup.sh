#!/usr/bin/env bash
# ABA — Linux installer bootstrap (install type 2). Run from a cloned checkout:
#   git clone https://github.com/kharchenkolab/aba.git
#   cd aba && ./install/linux/setup.sh                       # desktop: helper UI + browser
#   ./install/linux/setup.sh --headless                      # server / login node: no browser
#   ./install/linux/setup.sh --install-dir /opt/aba          # put the WHOLE install elsewhere (default ~/.aba)
#   ./install/linux/setup.sh --runtime-dir /data/$USER/aba   # just projects/data on a bigger disk
#   ./install/linux/setup.sh --cluster-personal --runtime-dir /shared/$USER/aba   # Slurm offload
#   ./install/linux/setup.sh --port 8100                     # backend port (default 8000; 2nd install on one host)
#
# aba + the recipe pack are public: the playbook git-clones both over https at
# $ABA_REF / $RECIPES_REF (default main) into $ABA_HOME/repo — the deployed repo is a
# real checkout, so `aba update` just git-pulls (one command, no external checkout).
# Overrides: ABA_REF / RECIPES_REF (pin a branch/tag/commit), ABA_REPO_URL /
# ABA_RECIPES_URL (alternate URLs), ABA_REPO_SRC / ABA_RECIPES_SRC (install from a
# LOCAL checkout — dev / offline), ABA_HOME.
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
    --port)                      ABA_PORT="${2:?--port needs a value}"; shift ;;
    -h|--help)                   sed -n '2,15p' "$0"; exit 0 ;;
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
# --port (or an exported ABA_PORT) bakes into the launcher (launcher.default_context reads
# ABA_PORT; default 8000) — a 2nd install on one host can pick a free port instead of :8000.
[ -n "${ABA_PORT:-}" ] && { export ABA_PORT; echo "   port:    $ABA_PORT (backend)"; }

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
# the *version*, and rather than fail we try, in order: the newest interpreter on
# PATH, then a SELF-CONTAINED Python we stand up via micromamba, and only as a LAST
# resort a Python environment-module (HPC). The helper venv is PERSISTENT — `aba
# update/doctor/auth/hpc-config` run it long after this shell exits — so a module
# python (whose libpython disappears once the module unloads) is avoided whenever we
# can build a self-contained one. Override the whole dance with ABA_PYTHON=/path/python3.
py_ver()    { "$1" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null; }
py_usable() { "$1" -c 'import sys,ensurepip; sys.exit(0 if sys.version_info[:2]>=(3,'"$MIN_PY_MINOR"') else 1)' >/dev/null 2>&1; }

# (modules) LAST resort: newest python module >=3.9 on an Lmod / environment-modules
# system. Used only if PATH and the micromamba bootstrap both fail — a module python
# isn't self-contained (its libpython is gone once the module unloads), so the NOTE
# below warns that later `aba` commands need the module loaded.
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

# (micromamba) preferred fallback when PATH has no modern Python: a SELF-CONTAINED
# Python that needs no system python or module — so the persistent helper venv keeps
# working after this shell exits. Reuses $ABA_HOME/bin/micromamba, so the later
# install-micromamba step is a no-op.
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
  # Prefer the self-contained micromamba python over a module one: the helper venv
  # built from it must keep working after this shell (and any loaded module) is gone.
  [ -n "$PYBOOT" ] || bootstrap_python_via_micromamba || true
  [ -n "$PYBOOT" ] || try_module_python               || true
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

# --- aba repo source ---
# aba is public: by default the playbook git-clones it over https into $REPO_DIR (like
# the recipe pack), so the deployed repo is a real checkout and `aba update` git-pulls.
# To install THIS working tree instead (dev / offline / un-pushed changes), export
# ABA_REPO_SRC=. before running — clone-repos then rsyncs from it. Not the default.
if [ -n "${ABA_REPO_SRC:-}" ]; then echo "   aba source: local checkout ($ABA_REPO_SRC)"; else echo "   aba source: git clone ${ABA_REPO_URL:-https://github.com/kharchenkolab/aba} @ ${ABA_REF:-main}"; fi
# create-env runs BEFORE clone-repos, so $REPO_DIR/aba isn't populated yet — hand it the
# env specs straight from this checkout (avoids a redundant raw-github fetch and works
# offline; the r-env step is post-clone but set it too for consistency).
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
  # Deployment-conditional base (docs/arch/envs.md): pick the CUDA torch build iff GPU
  # compute exists (a gpu partition), unless the admin pinned ABA_ACCELERATOR. Persist to
  # config.env (runtime reads it for gpu_usable) + export so create-env's
  # inject-accelerator.sh builds the matching base. Admin override: set ABA_ACCELERATOR
  # (+ optional ABA_CUDA_VERSION) before running, or edit config.env + rebuild the env.
  if [ -n "${ABA_ACCELERATOR:-}" ]; then
    ACCEL="$ABA_ACCELERATOR"                                   # explicit override wins
  elif command -v sinfo >/dev/null 2>&1 && sinfo -h -o '%G' 2>/dev/null | grep -qiE 'gpu'; then
    ACCEL="cuda"                                              # a gpu partition exists
  else
    ACCEL="cpu"
  fi
  write_cfg ABA_ACCELERATOR "$ACCEL"; export ABA_ACCELERATOR="$ACCEL"
  [ -n "${ABA_CUDA_VERSION:-}" ] && write_cfg ABA_CUDA_VERSION "$ABA_CUDA_VERSION"
  echo "-- accelerator: ABA_ACCELERATOR=$ACCEL ($([ "$ACCEL" = cuda ] && echo 'CUDA torch base' || echo 'CPU-only torch base')) --"
  # Build-on-a-GPU-node nudge: we're about to build a CUDA base, but if THIS install host has
  # no visible GPU, building is smoother from a GPU-compatible node — conda detects the GPU
  # (__cuda) directly and you can confirm torch.cuda on the spot. Installing from here still
  # works (create-env spoofs __cuda via CONDA_OVERRIDE_CUDA); this is just an easier-path hint.
  if [ "$ACCEL" = cuda ] && ! { command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; }; then
    echo "!! NOTE: GPU partition(s) detected, but this installer is running on a NON-GPU node."
    echo "   Building the CUDA base is easier from a GPU-compatible node — consider re-running"
    echo "   the installer inside an interactive GPU allocation, e.g.:"
    echo "       srun -p <gpu-partition> --gres=gpu:1 --pty bash     # then re-run this installer"
    echo "   (It still works from here — the build spoofs the GPU via CONDA_OVERRIDE_CUDA.)"
  fi
  if command -v sinfo >/dev/null 2>&1; then
    # The runtime discovers partitions + QOS + account LIVE (sinfo + sacctmgr) at
    # submit time, so no hpc.yaml is written by default. Show what it detects;
    # `aba hpc-config` writes an editable override later if you want to pin/reorder.
    "$PY" -m aba_installer.cli hpc-config --print || true
    echo "   (to pin a partition list / reorder QOS / force an account, run"
    echo "    'aba hpc-config' to write \$ABA_HOME/hpc.yaml, then edit it)"
  else
    echo "   (no sinfo here — the runtime queries the scheduler live at submit time;"
    echo "    or run 'aba hpc-config' on a submit-capable node to write an override)"
  fi
fi

# --- env-build strategy (misc/lazy_env_init.md) ---
# staged = start the server on a minimal base, then finish the scientific Python
# stack + R env in the background (personal); eager = full build before start
# (shared/cluster). cluster-personal is Slurm/shared → eager; local (incl. a
# headless single-user server) → staged. An explicit ABA_ENV_PREWARM wins. Persist
# to config.env (runtime + `aba update` read it) + export so the install playbook's
# create/complete steps see it.
if [ -n "${ABA_ENV_PREWARM:-}" ]; then PREWARM="$ABA_ENV_PREWARM"
elif [ "$PROFILE" = "cluster-personal" ]; then PREWARM="eager"
else PREWARM="staged"; fi
write_cfg ABA_ENV_PREWARM "$PREWARM"; export ABA_ENV_PREWARM="$PREWARM"
echo "   env prewarm: $PREWARM"

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
