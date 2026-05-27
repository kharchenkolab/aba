"""Bio system-prompt assembler.

Composes per-turn system prompts from a declared list of blocks. Each
block knows (a) which roles it applies to and (b) which tool, if any,
must be active for it to render. This lets `build_system(active_tools,
role=...)` deliver a primary-Guide prompt with recipes / scenarios /
plan_first, an advisor prompt with just identity + behavior, etc.

A3 (agent_conditioning_plan.md): per-role manifest filtering. The role
defaults to "primary" for the existing Guide call site.

In Pass C this is wrapped by core/manifest/system_prompt_assembler.py,
which composes Manifest.knowledge_text from prompt + memory + policy.
For now bio owns the assembly directly; the assembler call site in
guide.py imports `build_system` from here.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).parent
_BIO_ROOT = _HERE.parent  # backend/content/bio/


@lru_cache(maxsize=None)
def _prompt(name: str) -> str:
    """Read a prompt .md file once and cache. Stripped of trailing whitespace
    so concatenations don't double-blank."""
    return (_HERE / name).read_text().rstrip()


@lru_cache(maxsize=None)
def _bio_doc(relpath: str) -> str:
    """Read a bio doc (e.g. conventions.md) once and cache."""
    p = _BIO_ROOT / relpath
    return p.read_text().rstrip() if p.exists() else ""


def _capabilities_block(active_tools: list[dict]) -> str:
    """Dynamic block — list each active tool's name + first sentence of its
    description. Stays in code because it composes runtime state, not text."""
    if not active_tools:
        return ""
    lines = ["Your tools (use them directly for routine reads — don't ask permission):"]
    for t in active_tools:
        desc = " ".join((t.get("description") or "").split())
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {t['name']}: {first}.")
    return "\n".join(lines) + "\n\n" + _prompt("sandbox_libs.md")


@dataclass(frozen=True)
class _Block:
    """One slot in the system prompt.

    roles=None  → applies to every role
    roles={"primary"} → only the streaming, halt-capable Guide gets it
    required_tool=None → render unconditionally (subject to role)
    required_tool="present_plan" → only when that tool is in active_tools
    render(active_tools) → returns the block text, or "" to skip
    """
    name:          str
    roles:         Optional[frozenset[str]]
    required_tool: Optional[str]
    render:        Callable[[list[dict]], str]


def _md(name: str) -> Callable[[list[dict]], str]:
    """Closure that ignores active_tools and returns a static .md file."""
    return lambda _tools: _prompt(name)


def _conventions(_tools: list[dict]) -> str:
    body = _bio_doc("conventions.md")
    return ("### File conventions\n\n" + body) if body else ""


def _skills_index(_tools: list[dict]) -> str:
    """B2: per-turn skills catalog (names + 1-line descriptions). The
    agent uses `read_skill(name)` to expand the body on demand. Empty
    until bio.skills register at import time; the registry returns ''
    in that case, which the assembler drops."""
    from core.skills import skills_index_block
    return skills_index_block()


def _memory_index(_tools: list[dict]) -> str:
    """B3: per-project memory index (typed files). Returns '' when no
    memories exist yet — so a fresh project shows no memory header."""
    from core.memory import memory_index_block
    return memory_index_block()


_BLOCKS: tuple[_Block, ...] = (
    _Block("identity",     None,                   None,             _md("identity.md")),
    _Block("capabilities", frozenset({"primary"}), None,             _capabilities_block),
    _Block("recipes",      frozenset({"primary"}), None,             _md("recipes.md")),
    _Block("scenarios",    frozenset({"primary"}), "create_scenario", _md("scenarios.md")),
    _Block("behavior",     None,                   None,             _md("behavior.md")),
    _Block("conventions",  None,                   None,             _conventions),
    # Skills index — primary only, gated on read_skill so a deployment
    # that doesn't enable the tool also doesn't advertise the catalog.
    _Block("skills",       frozenset({"primary"}), "read_skill",     _skills_index),
    # Memory index — primary only, gated on read_memory. Always rendered
    # for primary turns when the tool is on; the block itself returns ''
    # when no memories exist, which the assembler drops.
    _Block("memory",       frozenset({"primary"}), "read_memory",    _memory_index),
    _Block("plan_first",   frozenset({"primary"}), "present_plan",   _md("plan_first.md")),
)


def build_system(active_tools: list[dict], role: str = "primary") -> str:
    """Assemble a role-appropriate system prompt for this turn.

    role defaults to "primary" (the Guide). Advisor roles (e.g.
    "skeptic", "methodologist") get a trimmed prompt — no recipes,
    no scenarios, no plan_first, and no capabilities listing when
    they have no tools."""
    names = {t["name"] for t in active_tools}
    parts: list[str] = []
    for blk in _BLOCKS:
        if blk.roles is not None and role not in blk.roles:
            continue
        if blk.required_tool is not None and blk.required_tool not in names:
            continue
        text = blk.render(active_tools)
        if text:
            parts.append(text)
    return "\n\n".join(parts)
