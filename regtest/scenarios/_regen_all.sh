#!/usr/bin/env bash
# Regenerate ALL scenario data/ from the committed deterministic generators.
#
# The data/ files are NOT committed (only the generators are) — run this once after
# a fresh clone, before running the test suite:
#     bash regtest/scenarios/_regen_all.sh
#
# Fixed seeds → the `expected`/planted-truth values in each scenario.yaml are
# reproducible. Most generators run under the scenario venv (rdkit/skimage/tifffile/
# Bio/anndata); the few that need scanpy fall back to the runtime venv. Override the
# interpreters with ABA_SCENARIO_VENV / ABA_RUNTIME_VENV if your paths differ.
#
# CACHING: a per-scenario generator is SKIPPED if its data/ is already populated,
# so a weekly regen doesn't re-hit the network for the 6 fetchers (alphafold, msa,
# etc.) — set ABA_REGEN_FORCE=1 to regenerate everything. The top-level generator
# is offline+deterministic (synthetic), so it always runs (cheap, byte-identical).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
SV="${ABA_SCENARIO_VENV:-/home/pkharchenko/aba/tools/scenario-venv/bin/python}"
RV="${ABA_RUNTIME_VENV:-/home/pkharchenko/aba/aba_runtime/.venv/bin/python}"
FORCE="${ABA_REGEN_FORCE:-}"
[ -x "$SV" ] || SV="$RV"
ok=0; fail=0; skip=0; failed=""

run() {  # try the scenario venv, fall back to the runtime venv (scanpy etc.)
  local dir="$1"
  ( cd "$dir" && "$SV" _make_data.py ) >/dev/null 2>&1 && return 0
  ( cd "$dir" && "$RV" _make_data.py ) >/dev/null 2>&1
}

echo -n "[top-level] "; if run "$HERE"; then echo ok; ok=$((ok+1)); else echo FAIL; fail=$((fail+1)); failed="$failed top-level"; fi
for g in "$HERE"/*/_make_data.py; do
  d="$(dirname "$g")"; n="$(basename "$d")"
  if [ -z "$FORCE" ] && [ -d "$d/data" ] && [ -n "$(ls -A "$d/data" 2>/dev/null)" ]; then
    echo "[$n] skip (data present)"; skip=$((skip+1)); continue
  fi
  echo -n "[$n] "; if run "$d"; then echo ok; ok=$((ok+1)); else echo FAIL; fail=$((fail+1)); failed="$failed $n"; fi
done
echo "=== regen: $ok ok, $skip cached-skip, $fail failed${failed:+ ($failed)} ==="
[ "$fail" = 0 ]
