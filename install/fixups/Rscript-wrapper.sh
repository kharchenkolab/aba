#!/bin/bash
# ABA Rscript wrapper — install/fixups copy.
#
# Bug history: the original conda-shipped Rscript ELF binary had its R_HOME
# path compiled in as `/workspace/aba/backend/envs/tools/lib/R`. After the
# runtime-tree move (2026-05-31), that path no longer existed and the ELF
# was renamed `Rscript.elf-broken`. A shell wrapper replaced it that did:
#
#     exec R --no-echo --no-restore --no-save "$@"
#
# This BREAKS positional-script invocation: when the agent ran
# `Rscript --vanilla seurat_integration.R`, the wrapper invoked
# `R --no-echo … --vanilla seurat_integration.R`, and R printed
# `ARGUMENT 'seurat_integration.R' __ignored__` because R does not accept a
# script as a positional arg — only Rscript does. Result: background R jobs
# exited 0 but ran NOTHING. (Live-session diagnosis 2026-06-08.)
#
# The "broken" ELF actually still works in-place because it autoresolves
# R_HOME from its own location. This wrapper delegates to it, with a
# shell-emulation fallback that routes script-arg vs -e correctly if the
# ELF is missing.
#
# Install path: /workspace/aba-runtime/envs/tools/bin/Rscript
# (or wherever the env's bin/ is — adjust REAL and R_BIN below.)

REAL=/workspace/aba-runtime/envs/tools/bin/Rscript.elf-broken
if [[ -x "$REAL" ]]; then
  exec "$REAL" "$@"
fi

# Fallback: emulate Rscript via R.
R_BIN=/workspace/aba-runtime/envs/tools/lib/R/bin/R
SCRIPT=""
ARGS=()
USER_ARGS=()
SAW_EXPR=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--expression)
      SAW_EXPR=1; ARGS+=("-e" "$2"); shift 2;;
    --args)
      shift; USER_ARGS=("$@"); break;;
    -*)
      ARGS+=("$1"); shift;;
    *)
      if [[ -z "$SCRIPT" && $SAW_EXPR -eq 0 ]]; then
        SCRIPT="$1"; shift
      else
        USER_ARGS+=("$1"); shift
      fi;;
  esac
done

if [[ -n "$SCRIPT" ]]; then
  exec "$R_BIN" --no-echo --no-restore --no-save "${ARGS[@]}" \
       -f "$SCRIPT" --args "${USER_ARGS[@]}"
else
  exec "$R_BIN" --no-echo --no-restore --no-save "${ARGS[@]}"
fi
