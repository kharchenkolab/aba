#!/usr/bin/env python3
"""Store-port ratchet (modularity_audit2 Phase 3.1): only backend/core/graph/ may
call `_conn()` (the raw sqlite handle). Everything else uses the store API
(find_entities / get_entity / count_entities / create_entity / ...). The current
callers are grandfathered in ALLOWLIST and burned down over time:
  - entity-store sites (figure_history/search/revisions) → find_entities + a future
    edge-port method for the wasRevisionOf walks;
  - other-table sites (checkpoint=runs/messages, tool_telemetry, budget_summary,
    projects, main.py) → their own future ports (out of 3.1's entity-store scope).
A NEW `_conn()` caller outside core/graph/ fails this check. Stdlib-only (CI)."""
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
# A real CALL shape — `with _conn()`, `_conn() as`, `_conn().`, `= _conn()` —
# not mid-sentence prose like "every _conn() the agent loop" (a docstring).
_CONN = re.compile(r"(with\s+_conn\s*\(|=\s*_conn\s*\(|_conn\s*\(\s*\)\s*(\.|as\b))")

ALLOWLIST = {
    # entity-store (migrate via find_entities + an edge-port method)
    "content/bio/graph/figure_history.py",
    "content/bio/graph/search.py",
    "content/bio/lifecycle/revisions.py",
    # other tables — their own future ports (not 3.1's entity-store scope)
    "core/projects.py",
    "core/runtime/checkpoint.py",
    "core/runtime/tool_telemetry.py",
    "core/summarize/budget_summary.py",
    "main.py",
}


def _calls_conn(text: str) -> bool:
    for line in text.splitlines():
        code = line.split("#", 1)[0]   # strip line comments (avoid prose matches)
        if _CONN.search(code):
            return True
    return False


def main():
    violations = []
    for py in BACKEND.rglob("*.py"):
        s = str(py)
        if "__pycache__" in s or "/core/graph/" in s:
            continue
        rel = str(py.relative_to(BACKEND))
        if rel in ALLOWLIST:
            continue
        if _calls_conn(py.read_text()):
            violations.append(rel)
    if violations:
        print("STORE-PORT VIOLATION: _conn() called outside core/graph/ (use the store API):")
        for v in sorted(violations):
            print(f"  {v}")
        sys.exit(1)
    print(f"store-port OK: _conn() confined to core/graph/ (+{len(ALLOWLIST)} grandfathered, burning down)")


if __name__ == "__main__":
    main()
