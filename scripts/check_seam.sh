#!/usr/bin/env bash
# Enforce the platform/content seam (arch3_plan.md §6).
# Run on every PR; exit nonzero if violated.
#
# Rules:
#   1. backend/core/ must not import from backend/content/.
#   2. backend/core/ must not name bio entity types by literal.
#   3. backend/core/ must not import any bio-named module.
#
# Escape hatch: `# noqa: seam` on the offending line (justify in PR).

set -e
ROOT="$(git rev-parse --show-toplevel)"
CORE="$ROOT/backend/core"

if [ ! -d "$CORE" ]; then
  echo "seam check skipped (no backend/core/)"; exit 0
fi

fail() { echo "SEAM VIOLATION: $*"; exit 1; }

# 1. core/ must not import from content/
if grep -rnE "^(from content|import content)" "$CORE" 2>/dev/null | grep -v "# noqa: seam"; then
  fail "backend/core/ imports from backend/content/"
fi

# 2. core/ must not name bio entity types as string literals in code
TYPES='figure|finding|claim|result|dataset|analysis|narrative'
if grep -rnE "['\"]($TYPES)['\"]" "$CORE" 2>/dev/null \
   | grep -vE "(# noqa: seam|^[^:]+:[0-9]+:\s*#)"; then
  fail "backend/core/ references bio entity-type names by literal"
fi

# 3. core/ must not import bio-named modules even if they live elsewhere
BIO_MODS='advisors|registry|orientation|scenarios|promote|proposals|knowhow|conditioning'
if grep -rnE "^(from|import) ($BIO_MODS)\b" "$CORE" 2>/dev/null | grep -v "# noqa: seam"; then
  fail "backend/core/ imports a bio-named module"
fi

echo "seam OK"
