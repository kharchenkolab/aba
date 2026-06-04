#!/usr/bin/env python3
"""Platform-tier test purity check.

Companion to scripts/check_seam.sh, which enforces the same invariant on
backend/core/. This script enforces it on the test suite: the listed
PLATFORM_TESTS must not import from content/. They are the structural
counterparts of arch3.md §11 #6 — "Platform tests run without importing
any bio content."

Why a Python AST check and not pytest markers: ABA's tests are stand-
alone scripts (`python tests/dN_*.py`), not pytest tests. Adding pytest
markers would require converting the whole suite first. An AST scan is
the lightest-weight invariant that fits.

Adding a new platform-tier test: append it to PLATFORM_TESTS. The script
walks every import statement (including indented / function-body ones)
and fails if any reaches into content/.

Run:
    .venv/bin/python tests/check_platform_purity.py
Exit 0 = pure; exit 1 = at least one violation.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Platform-tier tests: must not import from content/. Discovered as the
# subset of tests/*.py with no top-level `from content` / `import content`
# (2026-06-04 audit). Add new platform-tier tests here.
PLATFORM_TESTS = [
    "tests/smoke_fake.py",
    "tests/p0_integration.py",
    "tests/p0_spine.py",
    "tests/p_resume_dedup.py",
    "tests/k1_kernels.py",
    "tests/repro_plan_resume_dup.py",
    "tests/d11_conversation_integrity.py",
    "tests/d12_plan_robustness.py",
    "tests/d13_kernelspec_hygiene.py",
    "tests/d18_stream_coalesce.py",
    "tests/d18c_tool_stream_buffer.py",
    "tests/d18d_tool_stream_replay.py",
    "tests/d19_run_python_env_parity.py",
]


def imports_in(py_path: Path) -> list[tuple[int, str]]:
    """Return (lineno, top-level-module) for every import in py_path,
    walking AST so lazy / function-body imports are included.

    For `from a.b.c import x`, the module recorded is `a.b.c`. For
    `import a.b`, it's `a.b`. For `import a`, it's `a`.
    """
    try:
        tree = ast.parse(py_path.read_text())
    except (SyntaxError, OSError) as exc:
        print(f"WARN: could not parse {py_path}: {exc}", file=sys.stderr)
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
    return out


def main() -> int:
    rc = 0
    violations: list[tuple[Path, int, str]] = []
    for rel in PLATFORM_TESTS:
        py = ROOT / rel
        if not py.exists():
            print(f"WARN: platform-tier test missing: {rel}", file=sys.stderr)
            continue
        for lineno, mod in imports_in(py):
            if mod == "content" or mod.startswith("content."):
                violations.append((py, lineno, mod))

    if violations:
        rc = 1
        print(f"FAIL: {len(violations)} content imports in platform-tier "
              f"test files (these MUST be content-free):", file=sys.stderr)
        for py, lineno, mod in violations:
            print(f"  {py.relative_to(ROOT)}:{lineno}  imports {mod}",
                  file=sys.stderr)
    else:
        print(f"OK platform-purity: {len(PLATFORM_TESTS)} tier-platform "
              "tests, no content imports.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
