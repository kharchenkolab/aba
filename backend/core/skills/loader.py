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
    # Coarse facet for filtering + the in-prompt domain "map" (e.g. 'genomics',
    # 'pharmacology'). Flat, not a tree — complements BM25, doesn't replace it.
    # Derived from the recipes/<domain>/ subfolder (frontmatter overrides).
    domain:         str = ""
    # How the skill reaches the agent: 'always' (rendered in the system prompt
    # every turn — the curated core/ set) or 'local' (retrieval-gated via
    # search_skills — the recipes/ cookbook). NOT a per-file choice: it's
    # stamped from the registered root folder, so a generated recipe can never
    # promote itself into the always-on prompt tier.
    visibility:     str = "local"
    produces:       tuple[str, ...] = ()
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    resource_profile: str = ""
    # Provenance of the procedure (e.g. 'biomni:tool/genomics.py::…',
    # 'github:kharchenkolab/pagoda2 + vignette …'). Surfaced in the catalog UI.
    source:         str = ""
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


def _spec_from_text(text: str, source_path: str = "", *,
                    default_domain: str = "", visibility: str = "local") -> SkillSpec:
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
        domain=(str(fm.get("domain") or "").strip() or default_domain),
        visibility=visibility,
        produces=tuple(prod),
        parameter_schema=fm.get("parameter_schema") or {},
        resource_profile=str(fm.get("resource_profile") or "").strip(),
        source=str(fm.get("source") or "").strip(),
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


def register_skill_dir(path: str | Path, *, visibility: str = "local") -> int:
    """Walk a directory tree of .md skill files and register each one. Returns
    the number registered. Idempotent on re-registration (later wins so
    overlays can override). Also feeds the plan validator's KNOWN_SKILLS
    so 'unknown skill' warnings reference the real catalog.

    `visibility` is stamped on every file under this root (folder-driven, not
    per-file). Files in a subfolder take that subfolder's name as their default
    `domain` (frontmatter `domain:` still wins), so recipes/<domain>/foo.md is
    self-classifying."""
    from core.planning.validator import register_skill

    p = Path(path)
    if not p.is_dir():
        return 0
    n = 0
    for f in sorted(p.rglob("*.md")):
        rel = f.relative_to(p)
        # Immediate subfolder (if any) is the default domain facet.
        default_domain = rel.parts[0] if len(rel.parts) > 1 else ""
        text = f.read_text()
        try:
            spec = _spec_from_text(text, source_path=str(f),
                                   default_domain=default_domain, visibility=visibility)
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
        s.domain.replace("_", " "),
    ])


def skill_domains() -> list[str]:
    """Sorted distinct domains across the *searchable* (local) cookbook — the
    flat facet that backs the in-prompt domain map + the search_skills filter.
    Always-on core skills are listed in full and don't need a facet."""
    return sorted({s.domain for s in _REGISTRY.values()
                   if s.domain and s.visibility != "always"})


def _index():
    """Lazily (re)build the BM25 index over the current registry."""
    global _INDEX
    if _INDEX is None:
        from core.search import BM25
        _INDEX = BM25((s.name, _doc_text(s)) for s in list_skills())
    return _INDEX


def search_skills(query: str, *, limit: int = GATED_TOP_K,
                  domain: Optional[str] = None) -> list[SkillSpec]:
    """Intent-ranked skills (BM25), optionally filtered to one `domain` (the
    flat facet). Empty/whitespace query → first `limit` (within the domain, if
    given) — a stable default slice, not a relevance claim. Names that no longer
    resolve are skipped (registry mutated under us)."""
    dom = (domain or "").strip().lower()

    def _ok(s: SkillSpec) -> bool:
        return not dom or s.domain.lower() == dom

    q = (query or "").strip()
    if not q:
        return [s for s in list_skills() if _ok(s)][:limit]
    # Over-fetch then domain-filter so the cap still yields `limit` matches.
    hits = _index().search(q, limit=limit if not dom else max(limit * 5, 25))
    out = [_REGISTRY[i] for i, _ in hits if i in _REGISTRY and _ok(_REGISTRY[i])]
    return out[:limit]


def recipes_for_capability(cap: str) -> list[str]:
    """Names of recipes whose `capabilities_needed` includes `cap` (case-
    insensitive). Drives the run_python/run_r recipe-uptake nudge: if the agent
    codes with a library a recipe covers but didn't read that recipe this turn,
    remind it (the recipe has the correct API/idioms it would otherwise hand-roll
    from stale memory)."""
    c = (cap or "").strip().lower()
    if not c:
        return []
    return [s.name for s in list_skills()
            if any(c == (x or "").strip().lower() for x in (s.capabilities_needed or ()))]


def _skill_bullets(skills: list[SkillSpec]) -> list[str]:
    return [f"- `{s.name}` — {s.description}" if s.description else f"- `{s.name}`"
            for s in skills]


def _diversify(pool: list[SkillSpec], k: int, *, reserve: int = 3) -> list[SkillSpec]:
    """Pick k from a relevance-ranked pool, but don't let one domain take every
    slot. A single domain may fill at most k-reserve slots; the rest go to the
    next-best recipes from other domains. Without this, a domain-heavy phrasing
    (e.g. 'analyze PBMC scRNA-seq') fills all slots with analysis recipes and
    crowds out the action-relevant one (e.g. a GEO-fetch recipe ranked just
    outside the cap) — the exact gap that made the agent hand-roll + scrape."""
    cap = max(1, k - reserve)
    chosen: list[SkillSpec] = []
    counts: dict[str, int] = {}
    for s in pool:
        if len(chosen) >= k:
            break
        d = s.domain or "_"
        if counts.get(d, 0) < cap:
            chosen.append(s)
            counts[d] = counts.get(d, 0) + 1
    # If the cap left us short (small/narrow pool), backfill by pure relevance.
    if len(chosen) < k:
        for s in pool:
            if len(chosen) >= k:
                break
            if s not in chosen:
                chosen.append(s)
    return chosen[:k]


def skills_index_block(query: Optional[str] = None, limit: Optional[int] = None) -> str:
    """The skills slice embedded in the system prompt. Two tiers, by visibility:

      • Core (visibility 'always') — the curated operating + strategy skills,
        listed in full every turn so the agent never loses them.
      • Recipes (visibility 'local') — the domain cookbook, *retrieval-gated*:
        a small catalog (≤ FULL_LIST_MAX) is listed in full; past that, only the
        top-K relevant to `query` plus a `search_skills` pointer, so the prompt
        imprint stays bounded as the cookbook grows to hundreds.

    Anything in neither tier is discoverable remotely (search_nf_core /
    search_mcp_registry / search_pypi). Use read_skill(name) to expand a body.
    Returns '' when the registry is empty."""
    if not _REGISTRY:
        return ""
    q = (query or "").strip()
    all_skills = list_skills()
    core = [s for s in all_skills if s.visibility == "always"]
    cookbook = [s for s in all_skills if s.visibility != "always"]

    lines = [
        "### Skills you can reference by name",
        "Use `read_skill(name)` to load the full procedure. **If your task matches one of "
        "these recipes — especially anything using a specific library/tool or a multi-step "
        "method — `read_skill` it BEFORE writing run_python/run_r code.** The recipe carries "
        "the correct API, parameters, and gotchas; coding a known library from memory is the "
        "top cause of wrong-API fumbles here.",
    ]

    if core:
        lines += ["", "**Core skills** (always available):", *_skill_bullets(core)]

    if cookbook:
        total = len(cookbook)
        lines.append("")
        if total <= FULL_LIST_MAX:
            lines.append("**Recipes:**")
            shown = cookbook
        else:
            k = limit or GATED_TOP_K
            # Over-fetch a deeper pool (search_skills spans the whole registry),
            # drop core hits, then diversify by domain so one topic can't take
            # every slot and crowd out the action-relevant recipe.
            raw = search_skills(q, limit=k * 3 + len(core)) if q else []
            pool = [s for s in raw if s.visibility != "always"]
            shown = _diversify(pool, k)
            relevant = bool(q and shown)
            if not shown:  # no query, or no lexical overlap → stable default slice
                shown = cookbook[:k]
            rel = " most relevant to the current request" if relevant else ""
            lines.append(
                f"**Recipes** — showing {len(shown)} of {total}{rel}. "
                f"Call `search_skills(query)` to find others by intent."
            )
            doms = skill_domains()
            if doms:
                lines.append(f"Domains: {' · '.join(doms)} — narrow with search_skills(query, domain=…).")
        lines += _skill_bullets(shown)

    lines += ["",
              "Need a tool or pipeline that isn't listed? `search_nf_core`, "
              "`search_mcp_registry`, `search_pypi` / `search_bioconda` discover external ones."]
    return "\n".join(lines)
