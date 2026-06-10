"""Gate: pytest tests marked @pytest.mark.platform must not import content/.

Companion to tests/check_platform_purity.py — that script handles the
standalone-script tier (tests/dN_*.py); this pytest gate handles the
pytest-discoverable tier (tests/test_*.py marked platform).

Why both: the audit (misc/modularity_audit.md §3.8) flagged platform/bio
test coupling as the third Tier-1 leak. Wave 2 §5.1 makes pytest the
canonical test runner; this is the test-runner-side gate. New
pytest-discoverable tests marked @pytest.mark.platform get checked
here automatically — no enumeration required.

Decoupling note: this test parses module file paths via AST instead of
importing them, so it doesn't accidentally trigger the bio side-effects
the platform tests are designed to avoid. A platform test that imports
content via a transitive runtime-only path (very rare) won't be caught
by this surface check — for that, prefer importlib + sys.modules
inspection, which is heavier.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"


def _platform_marked_files() -> list[Path]:
    """AST-walk every tests/test_*.py and return those that carry a
    function decorated `@pytest.mark.platform` (function- or class-level).
    Module-level `pytestmark = pytest.mark.platform` is also honored."""
    out: list[Path] = []
    for py in sorted(TESTS.glob("test_*.py")):
        try:
            tree = ast.parse(py.read_text())
        except (SyntaxError, OSError):
            continue
        if _has_platform_marker(tree):
            out.append(py)
    return out


def _has_platform_marker(tree: ast.AST) -> bool:
    """True iff `tree` contains either a module-level pytestmark of
    `pytest.mark.platform` (single or tuple) or any function decorated
    with `@pytest.mark.platform`."""
    for node in ast.walk(tree):
        # Module-level: pytestmark = pytest.mark.platform
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "pytestmark":
                    if _attr_chain_ends(node.value, ("pytest", "mark", "platform")):
                        return True
                    # pytestmark = [pytest.mark.platform, ...]
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        for el in node.value.elts:
                            if _attr_chain_ends(el, ("pytest", "mark", "platform")):
                                return True
        # Decorator: @pytest.mark.platform
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in node.decorator_list:
                if _attr_chain_ends(d, ("pytest", "mark", "platform")):
                    return True
                # @pytest.mark.platform(...) — Call wrapping the attr chain.
                if isinstance(d, ast.Call) and _attr_chain_ends(d.func, ("pytest", "mark", "platform")):
                    return True
    return False


def _attr_chain_ends(node: ast.AST, chain: tuple[str, ...]) -> bool:
    """True iff `node` is an Attribute/Name chain that ends in `chain`.
    e.g. _attr_chain_ends(<pytest.mark.platform>, ('pytest','mark','platform'))."""
    parts: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return parts == list(chain)


def _imports_in(py_path: Path) -> list[tuple[int, str]]:
    """Return (lineno, top-level-module) for every import in py_path."""
    try:
        tree = ast.parse(py_path.read_text())
    except (SyntaxError, OSError):
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.append((node.lineno, alias.name))
    return out


@pytest.mark.platform
def test_platform_marked_tests_are_content_free():
    """Every tests/test_*.py carrying @pytest.mark.platform must NOT
    import content/. New platform-tier pytest tests get this check
    automatically — add the marker and it's gated.

    To opt-out (a test that needs bio): mark it @pytest.mark.bio
    instead.
    """
    violations: list[tuple[Path, int, str]] = []
    files = _platform_marked_files()
    assert files, (
        "no @pytest.mark.platform tests found — either the marker isn't "
        "being recognized, or no platform-tier pytest tests exist yet"
    )
    for py in files:
        for lineno, mod in _imports_in(py):
            if mod == "content" or mod.startswith("content."):
                violations.append((py, lineno, mod))
    if violations:
        msg = "\n".join(
            f"  {py.relative_to(ROOT)}:{lineno}  imports {mod}"
            for py, lineno, mod in violations
        )
        pytest.fail(
            f"{len(violations)} content imports in @pytest.mark.platform "
            f"tests (these MUST be content-free):\n{msg}"
        )


@pytest.mark.platform
def test_marker_self_check():
    """This test file itself is platform-marked and content-free —
    serves as the smoke baseline. If this fails to be picked up, the
    marker registration is broken."""
    me = Path(__file__).resolve()
    for lineno, mod in _imports_in(me):
        assert not mod.startswith("content"), \
            f"this test file imports content (line {lineno}: {mod})"
