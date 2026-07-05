"""Snapshot gate: the declared FastAPI route set is frozen against a golden file.

Item 2 (2A.0). The main.py/guide.py decomposition MOVES routes between modules
and onto new `APIRouter`s. This test scans EVERY web-route file (main.py + all
*.py under core/web/ and content/bio/web/) for route decorators and asserts the
full set of `(METHOD, path)` is identical to `tests/route_table.golden.txt`. A
route dropped, renamed, duplicated, or given the wrong method during a move
changes the set and fails loudly — regardless of which file it now lives in.

Why AST, not `import main:app`: a bare `import main` assembles the bio router
only partially (a circular-import artifact that conftest's pack registration
masks under pytest), so the live route table is unreliable outside a running
server. The AST scan is deterministic, dep-free, and import-order-independent.
It captures every *declared* route; "is it actually mounted" is covered by the
per-phase live smoke test.

Intentional route changes: regenerate with
    ABA_UPDATE_ROUTE_GOLDEN=1 python tests/test_route_table.py
and commit the golden diff (one reviewable line per endpoint).

Runs under pytest (CI) OR standalone (base env may lack pytest).
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

try:
    import pytest
    pytestmark = pytest.mark.platform
    def _fail(msg): pytest.fail(msg)
except ImportError:
    pytest = None
    def _fail(msg): raise AssertionError(msg)

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
MAIN = BACKEND / "main.py"
GOLDEN = Path(__file__).resolve().parent / "route_table.golden.txt"

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


def _route_files() -> list[Path]:
    files = [MAIN]
    for base in (BACKEND / "core" / "web", BACKEND / "content" / "bio" / "web"):
        if base.is_dir():
            files += [p for p in base.rglob("*.py") if p.name != "__init__.py"]
    legacy = BACKEND / "content" / "bio" / "web" / "routes.py"
    if legacy.exists() and legacy not in files:
        files.append(legacy)
    return sorted(set(files))


def _routes_in(py: Path) -> set[str]:
    """`{"METHOD /path"}` for every `@<name>.<method>("/path", ...)` decorator."""
    out: set[str] = set()
    tree = ast.parse(py.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if not isinstance(d, ast.Call) or not isinstance(d.func, ast.Attribute):
                continue
            method = d.func.attr.upper()
            if method not in _METHODS:
                continue
            if not isinstance(d.func.value, ast.Name):
                continue
            if not d.args or not isinstance(d.args[0], ast.Constant):
                continue
            path = d.args[0].value
            if isinstance(path, str):
                out.add(f"{method} {path}")
    return out


def _declared_routes() -> set[str]:
    routes: set[str] = set()
    for py in _route_files():
        routes |= _routes_in(py)
    return routes


def _load_golden() -> set[str]:
    if not GOLDEN.exists():
        return set()
    return {ln.strip() for ln in GOLDEN.read_text().splitlines() if ln.strip()}


def test_route_table_matches_golden():
    live = _declared_routes()
    golden = _load_golden()
    assert golden, (
        f"no route golden at {GOLDEN.relative_to(ROOT)} — generate it with "
        f"ABA_UPDATE_ROUTE_GOLDEN=1 python tests/test_route_table.py"
    )
    added = sorted(live - golden)
    removed = sorted(golden - live)
    if added or removed:
        lines = []
        if removed:
            lines.append("REMOVED (in golden, gone from source — did a move drop/rename it?):")
            lines += [f"  - {r}" for r in removed]
        if added:
            lines.append("ADDED (declared, not in golden — new route, or a rename's other half?):")
            lines += [f"  + {r}" for r in added]
        _fail(
            "route table drifted from golden:\n" + "\n".join(lines) +
            "\n\nIf intentional, regenerate: ABA_UPDATE_ROUTE_GOLDEN=1 "
            "python tests/test_route_table.py"
        )


if __name__ == "__main__":
    if os.environ.get("ABA_UPDATE_ROUTE_GOLDEN") == "1":
        routes = _declared_routes()
        GOLDEN.write_text("\n".join(sorted(routes)) + "\n")
        print(f"wrote {len(routes)} routes to {GOLDEN.relative_to(ROOT)}")
    else:
        test_route_table_matches_golden()
        print(f"PASS test_route_table_matches_golden ({len(_load_golden())} routes)")
