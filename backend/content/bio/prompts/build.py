"""Bio system-prompt assembler.

Composes per-turn system prompts from markdown blocks in this directory
plus a dynamic capabilities block built from the active tool set.

Order: identity → capabilities(active_tools) → recipes → [scenarios] →
behavior → [plan_first]. The bracketed blocks drop when their gating
tool is absent (disabled tools yield a tighter prompt).

In Pass C this is wrapped by core/manifest/system_prompt_assembler.py,
which composes Manifest.knowledge_text from prompt + memory + policy.
For now bio owns the assembly directly; the assembler call site in
guide.py imports `build_system` from here.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path

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
    lines = ["Your tools (use them directly for routine reads — don't ask permission):"]
    for t in active_tools:
        desc = " ".join((t.get("description") or "").split())
        first = desc.split(". ")[0].rstrip(".")
        lines.append(f"- {t['name']}: {first}.")
    return "\n".join(lines) + "\n\n" + _prompt("sandbox_libs.md")


def build_system(active_tools: list[dict]) -> str:
    """Assemble the Guide's system prompt for this turn."""
    names = {t["name"] for t in active_tools}
    blocks = [
        _prompt("identity.md"),
        _capabilities_block(active_tools),
        _prompt("recipes.md"),
    ]
    if "create_scenario" in names:
        blocks.append(_prompt("scenarios.md"))
    blocks.append(_prompt("behavior.md"))
    # File conventions — drives generated titles + future display paths.
    conventions = _bio_doc("conventions.md")
    if conventions:
        blocks.append("### File conventions\n\n" + conventions)
    if "present_plan" in names:
        blocks.append(_prompt("plan_first.md"))
    return "\n\n".join(blocks)
