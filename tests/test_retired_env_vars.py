"""Retired ABA_* env vars must have NO live references anywhere (env_reorg §6 reduction).

Complements test_env_registry_guard.py, which has two blind spots that let a retired var
linger and silently misbehave:
  * it scans `backend/` ONLY — a reference in `tools/`, `scripts/`, or `install/*.py`
    is invisible to it;
  * it deliberately ALLOWS writes — so a leftover SETTER isn't flagged.

Both bit us: after `ABA_DB_PATH_OVERRIDE` was merged into `ABA_DB_PATH`,
`tools/cleanup_shadow_figures.py` kept doing `os.environ["ABA_DB_PATH_OVERRIDE"] = …`,
which now no-ops → the tool operated on the DEFAULT workspace DB (deleting rows from the
wrong database under `--apply`).

This guard scans the WHOLE repo (.py) for ANY `os.environ`/`os.getenv` access — read,
write, `.get`, `getenv`, `in`, `.pop`, `.setdefault` — keyed on a retired name, and
fails. Comments / help strings are fine (AST only inspects env-access nodes). `tests/`
are excluded (they set/clear env by design).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parent.parent

# Vars removed / merged / derived by the env_reorg reduction — no live code may touch them.
RETIRED = {
    "ABA_DB_PATH_OVERRIDE": "merged into ABA_DB_PATH",
    "ABA_KERNEL_HARD_MAX": "derived from ABA_KERNEL_MAX_LIVE",
    "ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "resolved (redundant with lean_small)",
    "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS": "deleted",
    "ABA_OPENAI_OAUTH_CLIENT_ID": "hardcoded (mirrors the Anthropic client_id)",
}
SCAN_DIRS = ("backend", "tools", "scripts", "install")


def _os_names(tree: ast.AST) -> set[str]:
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "os":
                    names.add(a.asname or "os")
    return names or {"os"}


class _Finder(ast.NodeVisitor):
    def __init__(self, os_names: set[str]):
        self.os_names = os_names
        self.hits: list[tuple[int, str]] = []

    def _is_environ(self, node) -> bool:
        return (isinstance(node, ast.Attribute) and node.attr == "environ"
                and isinstance(node.value, ast.Name) and node.value.id in self.os_names)

    @staticmethod
    def _retired(node):
        if isinstance(node, ast.Constant) and node.value in RETIRED:
            return node.value
        return None

    def visit_Subscript(self, node):          # environ["X"] — read OR write target
        if self._is_environ(node.value):
            k = self._retired(node.slice)
            if k:
                self.hits.append((node.lineno, k))
        self.generic_visit(node)

    def visit_Call(self, node):               # os.getenv("X") / environ.get|pop|setdefault("X")
        f = node.func
        k = self._retired(node.args[0]) if node.args else None
        if k and isinstance(f, ast.Attribute):
            if f.attr == "getenv" and isinstance(f.value, ast.Name) and f.value.id in self.os_names:
                self.hits.append((node.lineno, k))
            elif f.attr in ("get", "pop", "setdefault") and self._is_environ(f.value):
                self.hits.append((node.lineno, k))
        self.generic_visit(node)

    def visit_Compare(self, node):            # "X" in os.environ
        if (len(node.ops) == 1 and isinstance(node.ops[0], ast.In)
                and self._is_environ(node.comparators[0])):
            k = self._retired(node.left)
            if k:
                self.hits.append((node.lineno, k))
        self.generic_visit(node)


def _scan(py: Path):
    try:
        tree = ast.parse(py.read_text(errors="replace"))
    except SyntaxError:
        return []
    f = _Finder(_os_names(tree))
    f.visit(tree)
    return f.hits


def test_no_references_to_retired_env_vars():
    offenders = {}
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.exists():
            continue
        for py in sorted(base.rglob("*.py")):
            if "/tests/" in str(py) or py.name.startswith("test_"):
                continue
            hits = _scan(py)
            if hits:
                offenders[str(py.relative_to(ROOT))] = hits
    assert not offenders, (
        "Retired ABA_* vars still accessed as env (merged/derived/deleted in env_reorg §6). "
        "Point the reference at the surviving name or delete the dead code:\n"
        + "\n".join(f"  {f}: {h}" for f, h in offenders.items())
        + "\n\nRetired: " + ", ".join(f"{k} ({v})" for k, v in RETIRED.items()))
