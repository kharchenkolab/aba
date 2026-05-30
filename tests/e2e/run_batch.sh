#!/usr/bin/env bash
# Sequentially run a set of e2e scenarios through the live (Sonnet) agent.
# Each scenario is isolated (own DB under /tmp/aba_scrna_suite) — DB-safe.
# Usage: run_batch.sh <worker_label> <scenario> [scenario ...]
set -u
cd /workspace/aba || exit 1
set -a; source /workspace/aba/.env 2>/dev/null; set +a
LABEL="$1"; shift
SBASE="${SUITE_BASE:-/tmp/aba_scrna_suite}"
MASTER="${SBASE}/MASTER_${LABEL}.log"
mkdir -p "$SBASE"
echo "=== worker ${LABEL} start  model=${ABA_MODEL:-?}  base=${SBASE}  scenarios: $* ===" > "$MASTER"
for scen in "$@"; do
  echo "[$(date +%H:%M:%S)] >>> $scen" >> "$MASTER"
  t0=$(date +%s)
  timeout 1500 /workspace/aba/.venv/bin/python -u tests/e2e/run_scrna_suite.py "$scen" \
    > "${SBASE}/run_${scen}.out" 2>&1
  rc=$?
  t1=$(date +%s)
  # Pull the autocheck verdict from the transcript if present.
  verdict=$(grep -h "AUTOCHECK" "${SBASE}/transcripts/${scen}.log" 2>/dev/null | tail -1)
  if [ $rc -eq 124 ]; then verdict="TIMEOUT(1500s) $verdict"; fi
  echo "[$(date +%H:%M:%S)] <<< $scen  rc=$rc  $((t1-t0))s  ${verdict}" >> "$MASTER"
done
echo "=== worker ${LABEL} DONE ===" >> "$MASTER"
