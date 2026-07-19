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
REPO="$(cd "$HERE/../.." && pwd)"

# Portable interpreter resolution (no hardcoded home). The SCENARIO venv needs
# rdkit/skimage/Bio/anndata/tifffile; the RUNTIME venv needs scanpy. Override either
# with ABA_SCENARIO_VENV / ABA_RUNTIME_VENV; else fall through common locations and,
# if nothing is usable, FAIL LOUD with guidance (never silently FAIL every generator).
_resolve_py() {  # $1=explicit override; then repo .venv, ABA_HOME/env, $PYTHON, python3
  local v="$1"; shift
  if [ -n "$v" ] && [ -x "$v" ]; then echo "$v"; return 0; fi
  for c in "$REPO/.venv/bin/python" "${ABA_HOME:-}/env/bin/python" "${PYTHON:-}" "$(command -v python3 2>/dev/null || true)"; do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}
SV="$(_resolve_py "${ABA_SCENARIO_VENV:-}")" || SV=""
RV="$(_resolve_py "${ABA_RUNTIME_VENV:-}")" || RV=""
if [ -z "$SV" ] && [ -z "$RV" ]; then
  echo "ERROR: no usable python. Set ABA_SCENARIO_VENV (a venv with rdkit/skimage/Bio/anndata/tifffile)" >&2
  echo "       and/or ABA_RUNTIME_VENV (a venv with scanpy), or put python3 on PATH." >&2
  exit 2
fi
[ -z "$SV" ] && SV="$RV"; [ -z "$RV" ] && RV="$SV"
# Upfront dep probe: warn (don't fail) so a bare-python3 fallback yields ONE clear
# message instead of N cryptic per-scenario FAILs downstream.
if ! "$SV" -c "import rdkit, skimage, Bio, anndata" >/dev/null 2>&1; then
  echo "NOTE: $SV lacks scenario deps (rdkit/skimage/Bio/anndata) — set ABA_SCENARIO_VENV to a venv" >&2
  echo "      that has them, or the chem/image/bio-parsing generators will FAIL (scanpy ones may still run)." >&2
fi
FORCE="${ABA_REGEN_FORCE:-}"
ok=0; fail=0; skip=0; failed=""

run() {  # try the scenario venv, fall back to the runtime venv (scanpy etc.)
  local dir="$1"
  ( cd "$dir" && "$SV" _make_data.py ) >/dev/null 2>&1 && return 0
  ( cd "$dir" && "$RV" _make_data.py ) >/dev/null 2>&1
}

# A scenario's data is "present" only if EVERY declared data_files entry exists — not
# merely that data/ is non-empty. An interrupted prior run can leave a PARTIAL data/
# (e.g. mystery.fasta written, blast_hits.tsv not); the old non-empty check skipped it,
# so the scenario ran against missing inputs and every step failed (blast_seq: 0/9).
_data_complete() {  # $1 = scenario dir
  local dir="$1" f files
  [ -d "$dir/data" ] || return 1
  files=$(awk '/^data_files:/{f=1;next} f&&/^-[[:space:]]/{sub(/^-[[:space:]]*/,"");print} f&&/^[^[:space:]-]/{f=0}' "$dir/scenario.yaml" 2>/dev/null)
  # no declared list → fall back to the old "non-empty" test
  [ -z "$files" ] && { [ -n "$(ls -A "$dir/data" 2>/dev/null)" ]; return $?; }
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    f="${f%\"}"; f="${f#\"}"; f="${f%\'}"; f="${f#\'}"
    [ -e "$dir/data/$f" ] || return 1
  done <<EOF
$files
EOF
  return 0
}

echo -n "[top-level] "; if run "$HERE"; then echo ok; ok=$((ok+1)); else echo FAIL; fail=$((fail+1)); failed="$failed top-level"; fi
for g in "$HERE"/*/_make_data.py; do
  d="$(dirname "$g")"; n="$(basename "$d")"
  if [ -z "$FORCE" ] && _data_complete "$d"; then
    echo "[$n] skip (data present)"; skip=$((skip+1)); continue
  fi
  echo -n "[$n] "; if run "$d"; then echo ok; ok=$((ok+1)); else echo FAIL; fail=$((fail+1)); failed="$failed $n"; fi
done
echo "=== regen: $ok ok, $skip cached-skip, $fail failed${failed:+ ($failed)} ==="
[ "$fail" = 0 ]
