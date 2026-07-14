"""Plane dependency-direction lint (weft rewrite W0.2 — misc/weft_rewrite.md §6.7).

The four-plane model (modularity2 / audit round 4): the WAIST (typed entity
graph + provenance semantics) is the hub every plane depends on — and it must
import NOTHING upward. This AST lint enforces that direction beside the
existing seams (`check_platform_purity` for core ↛ content,
`test_env_registry_guard` for inline env reads).

Waist here = `core/graph/`, `core/entity_types/`, `core/projects.py` — the
entity-model API. Upward planes:

  * Compute   — core/exec, core/jobs, core/compute (weft ports, W0.4)
  * Reasoning — guide, core/runtime, core/planning, core/skills, core/prompts,
                core/summarize, core/llm*, content.*
  * Contact   — core/web, main, core/viewers

Both top-level and lazy (in-function) imports count: a lazy import is still a
dependency, just a hidden one.

GRANDFATHERED (each names its planned dissolution — do NOT add entries without
a removal plan):
  * core/graph/exec_records.py → core.exec.env_manifest — the package-manifest
    dedup store. Dies in W2 when the exec record becomes a thin pointer to a
    weft EnvID (misc/weft_rewrite.md §4d).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

BACKEND = Path(__file__).resolve().parent.parent / "backend"

WAIST = [BACKEND / "core" / "graph", BACKEND / "core" / "entity_types",
         BACKEND / "core" / "projects.py"]

# Module prefixes the waist must not import (the upward planes).
UPWARD = (
    "core.exec", "core.jobs", "core.compute",                          # Compute
    "guide", "core.runtime", "core.planning", "core.skills",           # Reasoning
    "core.prompts", "core.summarize", "core.llm", "content",
    "core.web", "main", "core.viewers",                                # Contact
)

# (file relative to backend/, imported-module prefix) — see module docstring.
GRANDFATHERED = {
    ("core/graph/exec_records.py", "core.exec.env_manifest"),
}


def _imports_in(py: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(py.read_text(errors="replace"))
    except SyntaxError:
        return []
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append((node.lineno, a.name))
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append((node.lineno, node.module))
    return found


def _waist_files():
    for root in WAIST:
        if root.is_file():
            yield root
        else:
            yield from sorted(root.rglob("*.py"))


def test_waist_imports_nothing_upward():
    offenders: dict[str, list[tuple[int, str]]] = {}
    for py in _waist_files():
        rel = str(py.relative_to(BACKEND))
        hits = []
        for lineno, mod in _imports_in(py):
            if any(mod == p or mod.startswith(p + ".") for p in UPWARD):
                if (rel, mod) in GRANDFATHERED:
                    continue
                hits.append((lineno, mod))
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "The waist (entity graph) must not import upward planes "
        "(Compute/Reasoning/Contact) — depend on the waist, never from it. "
        "Offenders:\n"
        + "\n".join(f"  {f}: {hits}" for f, hits in offenders.items()))


def test_grandfather_list_is_live():
    """Every grandfathered edge must still exist — a stale entry means the
    debt was paid and the exception should be deleted."""
    stale = []
    for rel, mod in GRANDFATHERED:
        imports = {m for _, m in _imports_in(BACKEND / rel)}
        if mod not in imports:
            stale.append((rel, mod))
    assert not stale, f"grandfathered edges no longer exist — remove them: {stale}"
