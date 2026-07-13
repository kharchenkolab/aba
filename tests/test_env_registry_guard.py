"""The anti-bypass guard (env_reorg Phase 4) — the deliverable that makes the
registry trustworthy.

FAILS if any code in backend/ reads an `ABA_*` environment variable directly via
`os.environ`/`os.getenv` (or an aliased `os`) OUTSIDE the one allowed home,
`core/config.py`. Without this the registry silently rots back to inline reads and
`list_settings()` / `aba doctor` under-report the real surface.

Scope + exclusions (deliberate, documented):
  * Only `backend/` is guarded. The installer / OOD launcher shell (`install/`)
    legitimately reads `ABA_*` / `ABA_PF_*` as a deploy-time contract — out of scope.
  * `core/config.py` is the allowed read path (the registry lives there).
  * `tests/` are excluded — tests set/read env via monkeypatch by design.
  * WRITES are fine (`os.environ["ABA_X"] = ...`, `.pop`, `.setdefault`) — the
    registry READS what other code writes (credential set, model hot-swap).
  * `env.get("ABA_X")` where `env` is a passed-in dict (scope_resolver) is not an
    `os.environ` access and is not flagged.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

BACKEND = Path(__file__).resolve().parent.parent / "backend"
ALLOWLIST = {BACKEND / "core" / "config.py"}


def _aliased_os_names(tree: ast.AST) -> set[str]:
    """Names bound to the `os` module in this file (import os / import os as _os)."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "os":
                    names.add(a.asname or "os")
    return names or {"os"}


class _Finder(ast.NodeVisitor):
    def __init__(self, os_names: set[str]):
        self.os_names = os_names
        self.hits: list[tuple[int, str]] = []
        self._assign_targets: set[int] = set()

    def _is_environ(self, node) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "environ"
                and isinstance(node.value, ast.Name) and node.value.id in self.os_names)

    @staticmethod
    def _aba_key(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith("ABA_"):
            return node.value
        return None

    def visit_Assign(self, node):
        # Record environ[...] subscripts that are assignment TARGETS (writes) so we
        # don't flag them as reads.
        for t in node.targets:
            if isinstance(t, ast.Subscript) and self._is_environ(t.value):
                self._assign_targets.add(id(t))
        self.generic_visit(node)

    def visit_Call(self, node):
        f = node.func
        key = self._aba_key(node.args[0]) if node.args else None
        if key and isinstance(f, ast.Attribute):
            # os.getenv("ABA_X") / os.environ.get("ABA_X")
            if (f.attr == "getenv" and isinstance(f.value, ast.Name)
                    and f.value.id in self.os_names):
                self.hits.append((node.lineno, key))
            elif f.attr == "get" and self._is_environ(f.value):
                self.hits.append((node.lineno, key))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if self._is_environ(node.value) and id(node) not in self._assign_targets:
            key = self._aba_key(node.slice)
            if key:
                self.hits.append((node.lineno, key))
        self.generic_visit(node)

    def visit_Compare(self, node):
        if (len(node.ops) == 1 and isinstance(node.ops[0], ast.In)
                and self._is_environ(node.comparators[0])):
            key = self._aba_key(node.left)
            if key:
                self.hits.append((node.lineno, key))
        self.generic_visit(node)


def _scan(py: Path):
    try:
        tree = ast.parse(py.read_text(errors="replace"))
    except SyntaxError:
        return []
    f = _Finder(_aliased_os_names(tree))
    f.visit(tree)
    return f.hits


def test_no_inline_aba_env_reads_outside_config():
    offenders = {}
    for py in sorted(BACKEND.rglob("*.py")):
        if py in ALLOWLIST:
            continue
        if "/tests/" in str(py) or py.name.startswith("test_"):
            continue
        hits = _scan(py)
        if hits:
            offenders[str(py.relative_to(BACKEND))] = hits
    assert not offenders, (
        "Inline ABA_* env reads must go through the config registry "
        "(config.settings.<name>.get()). Offenders:\n"
        + "\n".join(f"  {f}: {hits}" for f, hits in offenders.items()))
