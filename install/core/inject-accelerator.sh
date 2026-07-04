#!/usr/bin/env bash
# Deployment-conditional base: inject the CUDA PyTorch pin into the conda env spec when
# the deployment declares a GPU accelerator, so scvi-tools resolves the GPU torch build
# instead of conda-forge's CPU-only default (the scVI-on-CPU incident — see
# docs/arch/envs.md). Single source of truth: environment.yml; this adds the accelerator
# variant at build time, so there is no drift-prone duplicate GPU env file.
#
# Driven by config.env (installer-written, admin-editable), read by the create-env step:
#   ABA_ACCELERATOR   cpu (default) | cuda
#   ABA_CUDA_VERSION  optional pin (e.g. 12.4), matched to the GPU nodes' driver
#
# Usage: inject-accelerator.sh <path/to/environment.yml>   (idempotent; no-op for cpu)
set -euo pipefail
YML="${1:?usage: inject-accelerator.sh <environment.yml>}"
ACCEL="${ABA_ACCELERATOR:-cpu}"

if [ "$ACCEL" != "cuda" ]; then
  echo "[accelerator] CPU base (ABA_ACCELERATOR=$ACCEL) — no GPU pin injected"
  exit 0
fi
if grep -q "pytorch-gpu" "$YML"; then
  echo "[accelerator] pytorch-gpu pin already present in $YML"
  exit 0
fi
# Insert the CUDA pins into the conda dependency list, right before the `- pip:` section
# (a stable marker). awk keeps this portable across GNU/BSD (sed -i differs).
tmp="$(mktemp)"
awk -v cv="${ABA_CUDA_VERSION:-}" '
  /^  - pip:/ { print "  - pytorch-gpu"; if (cv != "") print "  - cuda-version=" cv }
  { print }
' "$YML" > "$tmp" && mv "$tmp" "$YML"
echo "[accelerator] CUDA base: injected pytorch-gpu${ABA_CUDA_VERSION:+ + cuda-version=$ABA_CUDA_VERSION} into $YML"
