#!/usr/bin/env bash
# glibc-floor — is BASE_GLIBC too new for TARGET_GLIBC?
#
# SINGLE SOURCE OF TRUTH for the SIF base-image compatibility rule (used by
# install/sif/build.sh at build time and mirrored by the OOD preflight at launch).
#
# The SIF base image's glibc must be <= the compute nodes' glibc. Otherwise:
#   (a) anything compiled INSIDE the container links the newer glibc and fails on a
#       node with the older one ("version `GLIBC_2.xx' not found") — the classic
#       "works in the session, dies in the Slurm job" trap; and
#   (b) host environment-modules (an EL-built Lmod + its tools) won't load inside a
#       newer, different-userland container.
# conda-forge itself targets a glibc-2.17 (EL7) floor, so an EL7 base clears the bar
# on essentially any current cluster.
#
# Usage:  glibc-floor.sh <base_glibc> <target_glibc>
#   Accepts "glibc 2.17" or "2.17".
#   exit 0  → OVERSHOOT (base > target): INCOMPATIBLE — the caller should warn loudly.
#   exit 1  → OK (base <= target), or either version unknown/unparseable (never cry wolf).
set -u

_norm() { echo "${1:-}" | awk '{print $NF}' | grep -oE '[0-9]+\.[0-9]+' | head -1; }
b="$(_norm "${1:-}")"; t="$(_norm "${2:-}")"

[ -n "$b" ] && [ -n "$t" ] || exit 1     # unknown on either side → not an overshoot
[ "$b" = "$t" ] && exit 1                 # equal → fine

# Overshoot iff base sorts strictly after target by numeric major.minor.
top="$(printf '%s\n%s\n' "$b" "$t" | sort -t. -k1,1n -k2,2n | tail -1)"
[ "$top" = "$b" ]
