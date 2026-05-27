"""Markdown-with-frontmatter skill loader (arch3 + B2).

Each skill is one .md file:

    ---
    name: scrna-qc-clustering
    description: scanpy QC + first-pass clustering
    when_to_use: scRNA-seq, fresh dataset, need clusters / UMAP
    requires_tools: [run_python]
    produces: [umap.png, qc_summary.csv]
    ---

    # body — the procedure prose / pseudo-code that read_skill returns

Content packs call register_skill_dir(path) at import time; the loader
walks the directory, parses each file, populates an in-process registry,
and side-effects the plan validator's KNOWN_SKILLS set so plans that
reference a skill the agent doesn't actually have surface a warning.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

import yaml


@dataclass(frozen=True)
class SkillSpec:
    """One skill: identity + minimal metadata + body. Body is the
    procedural text returned by read_skill(name)."""
    name:           str
    description:    str
    when_to_use:    str = ""
    requires_tools: tuple[str, ...] = ()
    produces:       tuple[str, ...] = ()
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    resource_profile: str = ""
    body:           str = ""
    source_path:    str = ""           # for diagnostics; not part of identity


_SPLIT = "---"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Pull the leading `--- … ---` YAML block off a markdown file and
    return (frontmatter_dict, body). Files without frontmatter return
    ({}, full_text); files with malformed frontmatter raise ValueError
    so a typo in a checked-in skill file fails loudly at startup."""
    if not text.startswith(_SPLIT):
        return {}, text.strip()
    # Find closing fence on its own line
    rest = text[len(_SPLIT):]
    end_idx = rest.find("\n" + _SPLIT)
    if end_idx == -1:
        raise ValueError("unterminated frontmatter block")
    fm_raw = rest[:end_idx]
    body = rest[end_idx + len("\n" + _SPLIT):].lstrip("\n").strip()
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"frontmatter YAML parse error: {e}") from e
    if not isinstance(fm, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return fm, body


def _spec_from_text(text: str, source_path: str = "") -> SkillSpec:
    fm, body = _split_frontmatter(text)
    name = (fm.get("name") or "").strip()
    if not name:
        raise ValueError(f"skill {source_path or '?'} missing required `name`")
    req = fm.get("requires_tools") or ()
    if isinstance(req, str):
        req = (req,)
    prod = fm.get("produces") or ()
    if isinstance(prod, str):
        prod = (prod,)
    return SkillSpec(
        name=name,
        description=str(fm.get("description") or "").strip(),
        when_to_use=str(fm.get("when_to_use") or "").strip(),
        requires_tools=tuple(req),
        produces=tuple(prod),
        parameter_schema=fm.get("parameter_schema") or {},
        resource_profile=str(fm.get("resource_profile") or "").strip(),
        body=body,
        source_path=source_path,
    )


# In-process registry. Content packs populate it via register_skill_dir;
# get_skill/read_skill/list_skills read from it.
_REGISTRY: dict[str, SkillSpec] = {}


def register_skill_dir(path: str | Path) -> int:
    """Walk a directory of .md skill files and register each one. Returns
    the number registered. Idempotent on re-registration (later wins so
    overlays can override). Also feeds the plan validator's KNOWN_SKILLS
    so 'unknown skill' warnings reference the real catalog."""
    from core.planning.validator import register_skill

    p = Path(path)
    if not p.is_dir():
        return 0
    n = 0
    for f in sorted(p.glob("*.md")):
        text = f.read_text()
        try:
            spec = _spec_from_text(text, source_path=str(f))
        except ValueError as e:
            # A broken skill file is a content bug — surface it but don't
            # abort loading. Other valid skills should still register.
            print(f"[skills] skip {f.name}: {e}")
            continue
        _REGISTRY[spec.name] = spec
        register_skill(spec.name)
        n += 1
    return n


def list_skills() -> list[SkillSpec]:
    """All currently-registered skills, sorted by name."""
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def get_skill(name: str) -> Optional[SkillSpec]:
    return _REGISTRY.get(name)


def read_skill(name: str) -> Optional[str]:
    """Return the full body of the named skill, or None if absent. This
    is what the `read_skill` tool returns to the agent."""
    s = _REGISTRY.get(name)
    return s.body if s else None


def skills_index_block() -> str:
    """One line per registered skill — name + description. This is what
    the system-prompt assembler embeds so the agent knows what reusable
    procedures exist without paying for every body. Use read_skill(name)
    to expand one on demand. Returns '' when the registry is empty."""
    if not _REGISTRY:
        return ""
    lines = [
        "### Skills you can reference by name",
        "Use `read_skill(name)` to load the full procedure when needed.",
        "",
    ]
    for s in list_skills():
        if s.description:
            lines.append(f"- `{s.name}` — {s.description}")
        else:
            lines.append(f"- `{s.name}`")
    return "\n".join(lines)
