#!/bin/bash
# Fix #3 — Rscript wrapper handles positional-script invocation.
# Run: bash tests/test_rscript_wrapper.sh
set -euo pipefail
RSCRIPT=/workspace/aba-runtime/envs/tools/bin/Rscript
[[ -x "$RSCRIPT" ]] || { echo "SKIP: $RSCRIPT not present"; exit 0; }

TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
cat > "$TMP/script.R" <<'EOF'
cat("HELLO_FROM_SCRIPT\n")
cat("HOME:", R.home(), "\n")
EOF

# --- 1. positional script + --vanilla ---
OUT=$("$RSCRIPT" --vanilla "$TMP/script.R")
echo "$OUT" | grep -q "HELLO_FROM_SCRIPT" || { echo "FAIL: --vanilla scriptarg"; echo "$OUT"; exit 1; }
echo "$OUT" | grep -q "ARGUMENT.*__ignored__" && { echo "FAIL: still seeing ARGUMENT __ignored__"; exit 1; }
echo "  ok  --vanilla + script.R works"

# --- 2. positional script no --vanilla ---
OUT=$("$RSCRIPT" "$TMP/script.R")
echo "$OUT" | grep -q "HELLO_FROM_SCRIPT" || { echo "FAIL: bare scriptarg"; exit 1; }
echo "  ok  bare script.R works"

# --- 3. -e <expr> still works ---
OUT=$("$RSCRIPT" -e 'cat("FROM_E\n")')
echo "$OUT" | grep -q "FROM_E" || { echo "FAIL: -e mode broken"; exit 1; }
echo "  ok  -e expression works"

# --- 4. --version still works ---
OUT=$("$RSCRIPT" --version 2>&1)
echo "$OUT" | grep -q "Rscript (R) version" || { echo "FAIL: --version"; exit 1; }
echo "  ok  --version works"

# --- 5. NO ARGUMENT __ignored__ leakage anywhere ---
for case_args in "--vanilla $TMP/script.R" "$TMP/script.R" "-e cat(1)"; do
  OUT=$("$RSCRIPT" $case_args 2>&1 || true)
  if echo "$OUT" | grep -q "__ignored__"; then
    echo "FAIL: __ignored__ in output for args: $case_args"
    echo "$OUT" | head -3
    exit 1
  fi
done
echo "  ok  no __ignored__ leakage"

echo "all 5 Rscript wrapper checks passed"
