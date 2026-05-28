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
    # Catalog capabilities (libs/CLIs) the procedure uses — e.g. ('pydeseq2',
    # 'gseapy'). Distinct from requires_tools (agent tools like run_python):
    # this is the skill→capability linkage that drives the discovery funnel
    # (read_skill names them → ensure_capability fills any gaps).
    capabilities_needed: tuple[str, ...] = ()
    # Free search terms to widen intent-search recall beyond name/description
    # (synonyms, abbreviations, related concepts).
    keywords:       tuple[str, ...] = ()
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
    caps = fm.get("capabilities_needed") or ()
    if isinstance(caps, str):
        caps = (caps,)
    kw = fm.get("keywords") or fm.get("tags") or ()
    if isinstance(kw, str):
        kw = (kw,)
    return SkillSpec(
        name=name,
        description=str(fm.get("description") or "").strip(),
        when_to_use=str(fm.get("when_to_use") or "").strip(),
        requires_tools=tuple(req),
        capabilities_needed=tuple(str(c).strip() for c in caps if str(c).strip()),
        keywords=tuple(str(k).strip() for k in kw if str(k).strip()),
        produces=tuple(prod),
        parameter_schema=fm.get("parameter_schema") or {},
        resource_profile=str(fm.get("resource_profile") or "").strip(),
        body=body,
        source_path=source_path,
    )


# In-process registry. Content packs populate it via register_skill_dir;
# get_skill/read_skill/list_skills read from it.
_REGISTRY: dict[str, SkillSpec] = {}

# Lazily-built BM25 index over the registry; invalidated whenever the
# registry changes (cheap to rebuild at this scale).
_INDEX: Any = None


def _invalidate_index() -> None:
    global _INDEX
    _INDEX = None


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
    if n:
        _invalidate_index()
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


# Above this many registered skills, the in-prompt index stops listing every
# skill (that would grow unbounded as the recipe library reaches 100+) and
# switches to a retrieval-gated top-K slice + a pointer to search_skills.
FULL_LIST_MAX = 15
# How many to show in the gated (large-catalog) index.
GATED_TOP_K = 8


def _doc_text(s: SkillSpec) -> str:
    """Searchable text for one skill. Name is included both hyphenated and
    space-split so 'rna seq' matches 'bulk-rnaseq-de'."""
    return " ".join([
        s.name,
        s.name.replace("-", " ").replace("_", " "),
        s.description,
        s.when_to_use,
        " ".join(s.keywords),
        " ".join(s.capabilities_needed),
    ])


def _index():
    """Lazily (re)build the BM25 index over the current registry."""
    global _INDEX
    if _INDEX is None:
        from core.search import BM25
        _INDEX = BM25((s.name, _doc_text(s)) for s in list_skills())
    return _INDEX


def search_skills(query: str, *, limit: int = GATED_TOP_K) -> list[SkillSpec]:
    """Intent-ranked skills (BM25). Empty/whitespace query → first `limit`
    alphabetically (a stable default slice, not a relevance claim). Names that
    no longer resolve are skipped (registry mutated under us)."""
    q = (query or "").strip()
    if not q:
        return list_skills()[:limit]
    hits = _index().search(q, limit=limit)
    return [_REGISTRY[i] for i, _ in hits if i in _REGISTRY]


def skills_index_block(query: Optional[str] = None, limit: Optional[int] = None) -> str:
    """The skills slice embedded in the system prompt. Use read_skill(name)
    to expand a body on demand. Returns '' when the registry is empty.

    Scalability: a small catalog (≤ FULL_LIST_MAX) is listed in full (cheap,
    complete). Past that, the block is *retrieval-gated* — it shows only the
    top-K skills relevant to `query` (the turn's intent) plus a pointer to
    search_skills — so the prompt imprint stays bounded as the recipe library
    grows to hundreds. With no query and a large catalog it shows a stable
    default slice and leans on search_skills for the rest."""
    if not _REGISTRY:
        return ""
    total = len(_REGISTRY)
    q = (query or "").strip()
    header = [
        "### Skills you can reference by name",
        "Use `read_skill(name)` to load the full procedure when needed.",
    ]

    if total <= FULL_LIST_MAX:
        skills = list_skills()
    else:
        k = limit or GATED_TOP_K
        skills = search_skills(q, limit=k) if q else []
        # No query, or a query with no lexical overlap → a stable default
        # slice so the block is never bullet-less (the agent still gets a
        # foothold + the search_skills pointer for the rest).
        relevant = bool(q and skills)
        if not skills:
            skills = list_skills()[:k]
        rel = " most relevant to the current request" if relevant else ""
        header.append(
            f"Showing {len(skills)} of {total} skills{rel}. "
            f"Call `search_skills(query)` to find others by intent."
        )

    header.append("")
    lines = list(header)
    for s in skills:
        lines.append(f"- `{s.name}` — {s.description}" if s.description else f"- `{s.name}`")
    return "\n".join(lines)
