#!/usr/bin/env bash
# Module: r-bio — build the R toolchain in the shared tools env (r-base + Seurat +
# Bioconductor: DESeq2/edgeR/limma) and install the lstar R `.rds` viewer bridge.
# Idempotent: skips when the tools env already has Seurat. Shared by the reconciler
# (first-use / manual enable) and, historically, the installer playbook.
#
# Inputs (env, with defaults):
#   ABA_HOME          install root (default ~/.aba)
#   MAMBA             micromamba binary (default $ABA_HOME/bin/micromamba)
#   REPO_DIR          checkout root holding aba/ (default $ABA_HOME/repo)
#   TOOLS_ENV         tools env prefix (default $ABA_RUNTIME_DIR|$ABA_HOME/runtime /envs/tools)
#   ABA_R_ENV_YML_SRC r-environment.yml override
set -uo pipefail

ABA_HOME="${ABA_HOME:-$HOME/.aba}"
MAMBA="${MAMBA:-$ABA_HOME/bin/micromamba}"
REPO_DIR="${REPO_DIR:-$ABA_HOME/repo}"
TOOLS_ENV="${TOOLS_ENV:-${ABA_RUNTIME_DIR:-$ABA_HOME/runtime}/envs/tools}"
RYML="${ABA_R_ENV_YML_SRC:-$REPO_DIR/aba/install/core/r-environment.yml}"

if [ -x "$TOOLS_ENV/bin/Rscript" ] && [ -d "$TOOLS_ENV/lib/R/library/Seurat" ]; then
  echo "[r-bio] R tools env already present — skipping build"
else
  if [ ! -f "$RYML" ]; then
    echo "[r-bio] ERROR: r-environment.yml not found: $RYML" >&2; exit 1
  fi
  rm -rf "$TOOLS_ENV"; mkdir -p "$(dirname "$TOOLS_ENV")"
  if ! "$MAMBA" create -y -v --channel-priority strict -p "$TOOLS_ENV" -f "$RYML"; then
    echo "[r-bio] WARNING: R tools env build failed — backend will provision R on demand" >&2
    exit 1
  fi
fi

H="$REPO_DIR/aba/install/core/install-lstar-r.sh"
if [ -f "$H" ]; then
  bash "$H" "$TOOLS_ENV" "$MAMBA" \
    || echo "[r-bio] WARNING: lstar R viewer bridge failed (.rds viewing degraded)" >&2
else
  echo "[r-bio] no install-lstar-r.sh — skipping lstar R bridge"
fi
echo "[r-bio] complete"
