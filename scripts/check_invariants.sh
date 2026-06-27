#!/usr/bin/env bash
# Platform-modularity invariants (modularity2.md §8 "columns" — enforced, not
# convention). One entry point for devs + CI; exits nonzero if any is violated.
# PYTHON overrides the interpreter (CI: system python3; local: .venv/bin/python).
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"
PY="${PYTHON:-python3}"
fail=0
run() { local name="$1"; shift; echo "── $name"; if "$@"; then echo "  ✓ ok"; else echo "  ✗ FAIL"; fail=1; fi; }

run "seam — core/ does not import content/, name bio types, or import bio modules" bash scripts/check_seam.sh
run "platform purity — platform sources free of top-level content imports"          "$PY" tests/check_platform_purity.py
# 1B adds: access gate — no ungated entity mutation.

if [ "$fail" = 0 ]; then echo "ALL INVARIANTS OK"; else echo "INVARIANTS FAILED"; fi
exit $fail
