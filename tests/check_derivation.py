#!/usr/bin/env python3
"""Derivation invariant (modularity_audit2 §Phase 2C): every create_entity call in
backend/ supplies a typed derivation — either derivation=<...> or exec_id=<...>
(which auto-derives derivation=exec). The 2D backfill is the runtime safety net;
this is the build-time ratchet that catches a new un-threaded create site.
Stdlib-only (CI). Escape: add (relpath, lineno) to ALLOWLIST with justification."""
import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
ALLOWLIST: set[tuple[str, int]] = set()


def _has_kw(call, names):
    return any(kw.arg in names for kw in call.keywords if kw.arg)


def main():
    violations = []
    for py in BACKEND.rglob("*.py"):
        if "__pycache__" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if name == "create_entity" and not _has_kw(node, {"derivation", "exec_id"}):
                    rel = str(py.relative_to(BACKEND.parent))
                    if (rel, node.lineno) not in ALLOWLIST:
                        violations.append((rel, node.lineno))
    if violations:
        print("DERIVATION VIOLATION: create_entity call(s) without derivation= or exec_id=:")
        for rel, lineno in sorted(violations):
            print(f"  {rel}:{lineno}")
        sys.exit(1)
    print("derivation OK: every create_entity supplies derivation= or exec_id=")


if __name__ == "__main__":
    main()
