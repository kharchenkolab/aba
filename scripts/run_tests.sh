#!/usr/bin/env bash
# Per-file test runner.
#
# Most of ABA's tests are "script-style": they configure the runtime via
# os.environ at module-IMPORT time and assume they are the first thing imported
# (their docstrings say `Run: python tests/foo.py`). `pytest tests/` runs them all
# in ONE shared process, so their import-time env writes + module-level state
# (scribe overrides, projects.init() caches) cross-contaminate and many fail.
#
# This runner gives each file its OWN process, using each test's intended
# entrypoint:
#   - a file with a __main__ self-check  ->  python tests/foo.py
#   - a pure pytest file                 ->  python -m pytest tests/foo.py
#
# Usage:
#   scripts/run_tests.sh                          # all tests/test_*.py
#   scripts/run_tests.sh tests/test_x.py ...      # specific files
#   VERBOSE=1 scripts/run_tests.sh                # stream each file's output
set -u
cd "$(dirname "$0")/.."
PY="${PY:-.venv/bin/python}"

files=("$@")
if [ ${#files[@]} -eq 0 ]; then
  files=(tests/test_*.py)
fi

pass=0; fail=0; failed=()
for f in "${files[@]}"; do
  [ -f "$f" ] || continue
  # Fresh throwaway runtime per file: isolates each file AND gives a valid dir to
  # script-style tests that don't set their own ABA_RUNTIME_DIR (else they fall to
  # the /workspace default and PermissionError). A test that sets its own _tmp at
  # import simply overrides this.
  export ABA_RUNTIME_DIR
  ABA_RUNTIME_DIR="$(mktemp -d -t aba_rt_XXXXXX)"
  if grep -qE '__name__ == .__main__.' "$f"; then
    cmd=("$PY" "$f")                                   # script-style: its self-check
  else
    cmd=("$PY" -m pytest "$f" -q -p no:cacheprovider)  # pytest-style
  fi
  if [ "${VERBOSE:-}" = "1" ]; then
    echo "=== $f (${cmd[1]##*/} entrypoint) ==="
    if "${cmd[@]}"; then pass=$((pass+1)); else fail=$((fail+1)); failed+=("$f"); fi
  else
    if "${cmd[@]}" >/dev/null 2>&1; then pass=$((pass+1)); else fail=$((fail+1)); failed+=("$f"); fi
  fi
done

echo "per-file: $pass passed / $((pass+fail)) files"
if [ "$fail" -gt 0 ]; then
  printf '  FAIL  %s\n' "${failed[@]}"
fi
[ "$fail" -eq 0 ]
