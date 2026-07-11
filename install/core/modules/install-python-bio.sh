#!/usr/bin/env bash
# Module: python-bio — complete the scientific Python stack into the base env
# (scanpy, anndata, scvi-tools→PyTorch, leidenalg, UMAP, …). Idempotent:
# `micromamba env update` installs only the delta beyond the minimal boot base, so
# re-running is a fast no-op once complete. Shared by the reconciler (staged /
# first-use) and, historically, the installer playbook.
#
# Inputs (env, with defaults):
#   ABA_HOME         install root (default ~/.aba)
#   ENV_DIR          base conda env prefix (default $ABA_HOME/env)
#   MAMBA            micromamba binary (default $ABA_HOME/bin/micromamba)
#   ABA_ENV_YML      full manifest (default $ABA_HOME/environment.yml)
#   ABA_ACCELERATOR  cpu|cuda (default cpu); ABA_CUDA_VERSION for the cuda major
set -uo pipefail

ABA_HOME="${ABA_HOME:-$HOME/.aba}"
ENV_DIR="${ENV_DIR:-$ABA_HOME/env}"
MAMBA="${MAMBA:-$ABA_HOME/bin/micromamba}"
MANIFEST="${ABA_ENV_YML:-$ABA_HOME/environment.yml}"
STAGE_FILE="$ENV_DIR/.aba-base-stage"

if [ "$(cat "$STAGE_FILE" 2>/dev/null)" = ready ]; then
  echo "[python-bio] base already complete (stage=ready) — skipping"; exit 0
fi
if [ ! -f "$MANIFEST" ]; then
  echo "[python-bio] ERROR: manifest not found: $MANIFEST" >&2; exit 1
fi

printf completing > "$STAGE_FILE"
# micromamba links package dirs read-only; env update can't replace files without u+w.
chmod -R u+w "$ENV_DIR"/lib/python*/site-packages 2>/dev/null || true
if [ "${ABA_ACCELERATOR:-cpu}" = cuda ]; then
  export CONDA_OVERRIDE_CUDA="${ABA_CUDA_VERSION:-11.8}"
  echo "[python-bio] cuda base: CONDA_OVERRIDE_CUDA=$CONDA_OVERRIDE_CUDA"
fi

if PYTHONNOUSERSITE=1 PIP_USER=0 "$MAMBA" env update --channel-priority strict \
     -p "$ENV_DIR" -f "$MANIFEST"; then
  printf ready > "$STAGE_FILE"
  echo "[python-bio] complete (stage=ready)"
  exit 0
else
  echo "[python-bio] WARNING: completion incomplete — stage stays 'completing'; " \
       "the agent installs missing libraries on demand. Re-run to resume." >&2
  exit 1
fi
