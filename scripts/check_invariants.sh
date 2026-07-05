#!/usr/bin/env bash
# Platform-modularity invariants (modularity2.md §8 "columns" — enforced, not
# convention). One entry point for devs + CI; exits nonzero if any is violated.
# PYTHON overrides the interpreter (CI: system python3; local: .venv/bin/python).
set -uo pipefail
ROOT="$(git rev-parse --show-toplevel)"; cd "$ROOT"

# Resolve a Python that can actually PARSE the checkers. They use 3.9+ syntax
# (PEP 585 `set[...]`, `from __future__ import annotations`), so an ancient
# system `python3` (e.g. CLIP's /usr/bin/python3 = 3.6.8) fails them with
# confusing SyntaxErrors that look like real findings. Try $PYTHON, a repo
# .venv, then version-tagged interpreters; validate each is >=3.9.
_py_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2]>=(3,9) else 1)' >/dev/null 2>&1; }
PY=""
for _cand in "${PYTHON:-}" ./.venv/bin/python python3.12 python3.11 python3.10 python3 python; do
  [ -n "$_cand" ] || continue
  command -v "$_cand" >/dev/null 2>&1 || continue
  if _py_ok "$_cand"; then PY="$_cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "check_invariants: no Python >=3.9 on PATH (the checkers need 3.9+ syntax)." >&2
  echo "  Set PYTHON=/path/to/python3, e.g. the ABA env interpreter." >&2
  exit 2
fi
echo "(checkers run with $PY — $("$PY" --version 2>&1))"
fail=0
run() { local name="$1"; shift; echo "── $name"; if "$@"; then echo "  ✓ ok"; else echo "  ✗ FAIL"; fail=1; fi; }

run "seam — core/ does not import content/, name bio types, or import bio modules" bash scripts/check_seam.sh
run "platform purity — platform sources free of top-level content imports"          "$PY" tests/check_platform_purity.py
run "derivation — every create_entity supplies derivation= or exec_id="              "$PY" tests/check_derivation.py
run "store port — _conn() confined to core/graph/ (store API elsewhere)"              "$PY" tests/check_store_port.py
# access-gate (no ungated entity mutation) is enforced by the pytest test
# tests/test_project_pinning_coverage.py (all mutating routes + bio routes +
# exemption table) — run via pytest in CI; not duplicated here (needs pytest).

if [ "$fail" = 0 ]; then echo "ALL INVARIANTS OK"; else echo "INVARIANTS FAILED"; fi
exit $fail
