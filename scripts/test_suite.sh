#!/usr/bin/env bash
# Run the unit suite with the isolation its own design assumes, and gate on
# a KNOWN-FAILURES baseline so a new red is visible.
#
# WHY PER-FILE PROCESSES. tests/conftest.py gives each test MODULE its own
# runtime dirs and its own SQLite DB ("the one-DB-per-script model"), because
# many tests are script-style: they configure the runtime at import time and
# carry state deliberately from one test to the next within a file. Run all
# files in ONE process and that model breaks down — `projects.set_current`
# repoints the process-global DB, module env re-pinning only happens for
# files that define `_tmp`, and files start reading each other's state.
#
# Measured 2026-08-25: one combined process reported 259 failures; 154 of
# them PASSED when their file ran alone. That 59% noise floor is not a
# curiosity — it is what hid a real, red guard naming the exact line of a
# production bug (weft_submitter.py:456) until a field report found it the
# hard way. A suite whose red means "maybe" cannot protect anything.
#
#   scripts/test_suite.sh                  # run + gate
#   scripts/test_suite.sh --update-baseline
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"; [ -x "$PY" ] || PY="$(command -v python3)"
BASELINE="tests/KNOWN_FAILURES.txt"
UPDATE=""; [ "${1:-}" = "--update-baseline" ] && UPDATE=1
OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT

FILES=$(find tests backend/tests -name 'test_*.py' -not -path '*/node_modules/*' | sort)
n=0; total=$(echo "$FILES" | wc -l)
for f in $FILES; do
  n=$((n+1)); printf '\r  [%3d/%3d] %-58s' "$n" "$total" "$(basename "$f")" >&2
  timeout 600 "$PY" -m pytest "$f" -q -p no:cacheprovider 2>/dev/null \
    | grep '^FAILED' | sed 's/ - .*//;s/^FAILED //' >> "$OUT"
done
printf '\r%-76s\r' '' >&2
sort -u "$OUT" -o "$OUT"

if [ -n "$UPDATE" ]; then
  cp "$OUT" "$BASELINE"; echo "baseline updated: $(wc -l < "$BASELINE") known failures"; exit 0
fi
[ -f "$BASELINE" ] || { echo "no $BASELINE — run with --update-baseline once" >&2; exit 2; }

NEW=$(comm -13 "$BASELINE" "$OUT"); FIXED=$(comm -23 "$BASELINE" "$OUT")
echo "failures: $(wc -l < "$OUT")   baseline: $(wc -l < "$BASELINE")"
rc=0
if [ -n "$NEW" ]; then
  echo; echo "REGRESSIONS — failing and not in the baseline:"; echo "$NEW" | sed 's/^/  /'; rc=1
fi
if [ -n "$FIXED" ]; then
  echo; echo "NOW PASSING — delete these from $BASELINE (the baseline may only shrink):"
  echo "$FIXED" | sed 's/^/  /'; rc=1
fi
[ "$rc" = 0 ] && echo "OK — no regressions, baseline exact."
exit $rc
