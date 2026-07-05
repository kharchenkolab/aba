"""Bundle loader: composes scope bundles into an EffectiveBundle.

Implements the algorithm specified in misc/bundle_layering.md. The
algorithm operates on an ordered list of scopes (the scope chain
provided by scope_resolver.ScopeResolution) and is scope-count-
agnostic — adding new scopes is purely a resolver concern.

Composition operations:
  - AGENTS.md / CLAUDE.md: concatenate (additive, broadest-first)
  - rules/required/*.md:   concatenate additively, including same-name across scopes
  - rules/*.md (loose):    override by filename, narrowest wins
  - skills/*:              override by skill name, narrowest wins; agents-filter; disable_recipes
  - catalog/*.yaml:        capabilities override by name (narrowest wins); R-base packages extend; collections register
  - settings.yaml/json:    deep dict-merge, scalars narrowest-wins, lists extend (default)
  - commands/, hooks/:     skipped by ABA (reserved for Claude Code consumers)

The loader is defensive: a malformed scope (e.g. unparseable YAML) is
skipped with a warning rather than raising — one broken scope doesn't
down the whole stack.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.bundle.scope_resolver import ScopeBundle, ScopeResolution


# -----------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------

@dataclass
class Rule:
    """One markdown file from rules/ or rules/required/."""
    filename: str                       # e.g. "figure_style.md"
    content: str                        # body (frontmatter stripped)
    source_scope: str
    frontmatter: dict | None = None


@dataclass
class Skill:
    """One skill (folder or flat)."""
    name: str                           # canonical id
    path: Path                          # SKILL.md (folder) or .md (flat)
    body: str                           # markdown body (frontmatter stripped)
    frontmatter: dict
    source_scope: str
    is_folder: bool                     # True for folder skills
    # Visibility tier — folder-driven, never per-file: skills/core/* → 'always'
    # (rendered in the system prompt every turn), everything else → 'local'
    # (searchable cookbook). A generated/lab recipe outside core/ can't promote
    # itself into the always-on tier.
    visibility: str = "local"
    # Flat facet for the in-prompt domain map + search filter, from
    # skills/recipes/<domain>/… (empty otherwise; frontmatter `domain:` can win
    # downstream in the live SkillSpec).
    domain: str = ""
    # Content tier for retrieval: 'recipe' (executable how-to, discovered from a
    # scope's skills/ tree) vs 'knowhow' (broad decision guide, from knowhow/).
    # Directory-derived, NEVER from frontmatter — so a draft's inert
    # `kind: knowhow_draft` can't mislabel the tier.
    kind: str = "recipe"


@dataclass
class CatalogEntry:
    """One capability spec from a scope's catalog/ dir (capabilities.md §4.2).
    The bio seeder projects these into the live per-project catalog, exactly as
    content.bio.skills projects EffectiveBundle.skills into the skill registry."""
    name: str
    spec: dict                          # the full capability spec (name, archetype, provisioning, …)
    source_scope: str


@dataclass
class Provenance:
    """Records of what each scope contributed + what was shadowed."""
    policy_scopes: list[str] = field(default_factory=list)
    required_files: dict[str, list[str]] = field(default_factory=dict)
    overrideable_files: dict[str, dict] = field(default_factory=dict)
    skills: dict[str, dict] = field(default_factory=dict)
    capabilities: dict[str, dict] = field(default_factory=dict)
    refsources: dict[str, dict] = field(default_factory=dict)
    settings_keys: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class EffectiveBundle:
    """The result of composition. Consumed by prompt/skill assembly.

    `policy_blocks` is the per-scope breakdown: list of (scope_name,
    label, content) tuples in scope-chain order. `policy_text` is the
    rendered concatenation (matches the spec format). Callers that need
    only some scopes (e.g. "everything except system") use
    `policy_text_excluding()`.
    """
    policy_blocks: list[tuple[str, str, str]] = field(default_factory=list)  # (scope_name, label, content)
    required_rules: list[Rule] = field(default_factory=list)
    overrideable_rules: list[Rule] = field(default_factory=list)
    skills: list[Skill] = field(default_factory=list)
    # Capability catalog, composed from each scope's catalog/ dir. The bio
    # seeder projects `catalog` into the per-project entity store; `r_base_specs`
    # is the curated shared-R-base conda list; `collection_dirs` are file-backed
    # reference collections (e.g. biomni) to register for search.
    catalog: list[CatalogEntry] = field(default_factory=list)
    r_base_specs: list[str] = field(default_factory=list)
    collection_dirs: list[Path] = field(default_factory=list)
    # Reference-source provider catalog (refsources.md / misc/refs.md §5.1),
    # composed from each scope's knowhow/refsources/ — override by provider name,
    # narrowest wins (exactly like `catalog`). refsources.py consumes this map;
    # it does NO layering of its own.
    refsources: dict[str, dict] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    provenance: Provenance = field(default_factory=Provenance)

    @property
    def policy_text(self) -> str:
        """Full rendered policy text (matches the layering spec)."""
        return _render_policy(self.policy_blocks)

    def policy_text_excluding(self, exclude_scopes: set[str]) -> str:
        """Rendered policy with named scopes filtered out. Useful when
        certain scopes' content is being injected via another path
        (e.g. the system scope's content already shows up via build.py's
        existing _Block reads of identity.md / behavior.md / etc.)."""
        kept = [(n, l, c) for n, l, c in self.policy_blocks
                if n not in exclude_scopes]
        return _render_policy(kept)

    def rules_excluding(self, exclude_scopes: set[str]) -> list[Rule]:
        """Required + overrideable rules with named scopes filtered out.
        Useful for the same reason as policy_text_excluding."""
        out: list[Rule] = []
        for r in self.required_rules + self.overrideable_rules:
            if r.source_scope not in exclude_scopes:
                out.append(r)
        return out

    def rule_content(self, filename: str) -> str | None:
        """Composed content for one rule filename, as build.py's named rule
        blocks consume it: an overrideable rule → the narrowest-scope winner;
        a required rule → all scopes' copies concatenated (additive). None when
        no scope provides it (caller falls back to the on-disk file)."""
        for r in self.overrideable_rules:
            if r.filename == filename:
                return r.content
        req = [r.content.rstrip() for r in self.required_rules if r.filename == filename]
        if req:
            return "\n\n".join(req)
        return None

    def system_policy(self) -> str:
        """The system scope's AGENTS.md policy block (what build.py's identity
        block injects). '' if the system scope contributed no policy."""
        return next((c for n, _l, c in self.policy_blocks if n == "system"), "")


def _render_policy(blocks: list[tuple[str, str, str]]) -> str:
    """Concatenation rules from misc/bundle_layering.md:
    - 0 blocks → empty string
    - 1 block  → just the body, no section header (matches existing
      build.py output style)
    - 2+ blocks → each with a "## <label> policy" header
    """
    if not blocks:
        return ""
    if len(blocks) == 1:
        return blocks[0][2].rstrip() + "\n"
    parts = [f"## {label} policy\n\n{content.rstrip()}\n" for _, label, content in blocks]
    return "\n".join(parts)


# -----------------------------------------------------------------------
# Frontmatter parsing
# -----------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<fm>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL
)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split YAML frontmatter from markdown body. Returns ({}, body) when
    no frontmatter is present.

    NB: intentionally NOT the canonical `core.frontmatter.parse_frontmatter`
    (burn-down #4 deduped the two *strict* parsers). This one is deliberately
    LENIENT — regex-matched, never raises, and preserves the body verbatim
    (no strip) — because bundle snippets/AGENTS.md must load best-effort even
    with a malformed header, and their body whitespace is significant."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        import yaml
        fm = yaml.safe_load(m.group("fm")) or {}
        if not isinstance(fm, dict):
            fm = {}
    except Exception:
        fm = {}
    return fm, m.group("body")


# -----------------------------------------------------------------------
# AGENTS.md / @path imports
# -----------------------------------------------------------------------

_IMPORT_RE = re.compile(r"^@([\w./\-]+)\s*$", re.MULTILINE)
_MAX_IMPORT_DEPTH = 4


def _resolve_imports(text: str, base_dir: Path,
                      depth: int = 0, seen: set | None = None) -> str:
    """Inline-resolve @path imports (Claude Code convention)."""
    if depth >= _MAX_IMPORT_DEPTH:
        return text
    if seen is None:
        seen = set()

    def _sub(m: re.Match) -> str:
        rel = m.group(1)
        p = (base_dir / rel).resolve()
        if p in seen:
            return f"<!-- circular import: {rel} -->"
        seen.add(p)
        if not p.is_file():
            return f"<!-- import not found: {rel} -->"
        try:
            content = p.read_text()
        except Exception:
            return f"<!-- import unreadable: {rel} -->"
        return _resolve_imports(content, p.parent, depth + 1, seen)

    return _IMPORT_RE.sub(_sub, text)


def _read_policy_md(scope_path: Path) -> str | None:
    """Read AGENTS.md (preferred) or CLAUDE.md (bridge fallback).
    Returns None if neither exists.

    If a scope has both AGENTS.md and a CLAUDE.md whose only content is
    `@AGENTS.md`, we prefer AGENTS.md (no duplication). If CLAUDE.md
    has independent content, we still ignore it — the file is meant to
    be a one-line bridge."""
    agents = scope_path / "AGENTS.md"
    claude = scope_path / "CLAUDE.md"
    if agents.is_file():
        text = agents.read_text()
        return _resolve_imports(text.strip(), agents.parent)
    if claude.is_file():
        text = claude.read_text()
        return _resolve_imports(text.strip(), claude.parent)
    return None


# -----------------------------------------------------------------------
# Per-subsystem composition
# -----------------------------------------------------------------------

def _compose_policy_blocks(chain: list[ScopeBundle],
                            provenance: Provenance,
                            ) -> list[tuple[str, str, str]]:
    """Read AGENTS.md/CLAUDE.md across scopes; return ordered per-scope
    blocks as (scope_name, label, content) tuples. Rendering is delegated
    to `_render_policy` so callers can filter blocks before render."""
    blocks: list[tuple[str, str, str]] = []
    for s in chain:
        if not s.present:
            continue
        content = _read_policy_md(s.path)
        if content:
            blocks.append((s.name, s.label, content))
            provenance.policy_scopes.append(s.name)
    return blocks


def _compose_required_rules(chain: list[ScopeBundle],
                             provenance: Provenance) -> list[Rule]:
    """Additive across all scopes; include same-name files from
    multiple scopes."""
    out: list[Rule] = []
    for s in chain:
        if not s.present:
            continue
        rdir = s.path / "rules" / "required"
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.glob("*.md")):
            try:
                text = f.read_text()
            except Exception as e:
                provenance.warnings.append(
                    f"{s.name}: unreadable {f.name}: {e}")
                continue
            fm, body = _parse_frontmatter(text)
            out.append(Rule(
                filename=f.name, content=body,
                source_scope=s.name, frontmatter=fm,
            ))
            provenance.required_files.setdefault(f.name, []).append(s.name)
    return out


def _compose_overrideable_rules(chain: list[ScopeBundle],
                                 provenance: Provenance) -> list[Rule]:
    """Per-filename override: narrowest scope wins.

    Walk the chain in reverse (narrowest first); add each filename
    once. Shadowed scopes are recorded in provenance."""
    seen: dict[str, Rule] = {}
    shadowed: dict[str, list[str]] = {}
    for s in reversed(chain):
        if not s.present:
            continue
        rdir = s.path / "rules"
        if not rdir.is_dir():
            continue
        for f in sorted(rdir.glob("*.md")):
            # exclude rules/required/* (handled separately)
            if f.parent.name == "required" or "required" in [p.name for p in f.parents][:2]:
                continue
            if f.name in seen:
                shadowed.setdefault(f.name, []).append(s.name)
                continue
            try:
                text = f.read_text()
            except Exception as e:
                provenance.warnings.append(
                    f"{s.name}: unreadable {f.name}: {e}")
                continue
            fm, body = _parse_frontmatter(text)
            seen[f.name] = Rule(
                filename=f.name, content=body,
                source_scope=s.name, frontmatter=fm,
            )

    # Record provenance
    for fname, rule in seen.items():
        provenance.overrideable_files[fname] = {
            "effective_scope": rule.source_scope,
            "shadowed_in": shadowed.get(fname, []),
        }

    # Output in deterministic order (filename sort)
    return sorted(seen.values(), key=lambda r: r.filename)


def _skill_canonical_name(path: Path, frontmatter: dict,
                            *, is_folder: bool) -> str:
    """The canonical identifier for a skill."""
    name = frontmatter.get("name") if isinstance(frontmatter, dict) else None
    if name:
        return str(name).strip()
    if is_folder:
        return path.parent.name        # dir name
    return path.stem                   # filename without .md


def _skill_tier(rel_parts: tuple) -> tuple[str, str]:
    """(visibility, domain) from a skill path relative to skills/.
    skills/core/**            → ('always', '')   (system-prompt tier)
    skills/recipes/<domain>/** → ('local', '<domain>')
    everything else           → ('local', '')"""
    vis = "always" if rel_parts and rel_parts[0] == "core" else "local"
    dom = rel_parts[1] if (len(rel_parts) >= 3 and rel_parts[0] == "recipes") else ""
    return vis, dom


def _read_skill_fm(path: Path, scope: ScopeBundle,
                   provenance: Provenance) -> tuple[dict, str] | None:
    """Read + parse one skill file's frontmatter, or return None (skip) with a
    LOUD provenance warning. `_parse_frontmatter` is lenient — it returns {} for
    BOTH 'no frontmatter' and 'malformed YAML'. A malformed-YAML file would then
    become a nameless Skill that dies far downstream as a misleading 'missing
    name' skip. We catch it here: a file that opens a `---` fence but yields an
    empty mapping has broken frontmatter (e.g. an unquoted `key: value` colon in
    a description) — flag it so a broken skill never silently vanishes."""
    try:
        text = path.read_text()
    except Exception as e:
        provenance.warnings.append(f"{scope.name}: unreadable {path}: {e}")
        return None
    fm, body = _parse_frontmatter(text)
    if text.lstrip().startswith("---") and not fm:
        provenance.warnings.append(
            f"{scope.name}: malformed/empty frontmatter — skill dropped "
            f"(check for an unquoted ':' in a value): {path}")
        return None
    return fm, body


def _discover_skill_tree(root: Path, scope: ScopeBundle, provenance: Provenance,
                         *, kind: str, tier_of, skip=None) -> list[Skill]:
    """Walk one markdown-skill tree (recursively, following symlinks). Recognizes
    folder skills (<dir>/SKILL.md) and flat skills (<name>.md). `tier_of(rel_parts,
    fm) -> (visibility, domain)` decides the tier per path; `skip(path) -> bool`
    excludes files (e.g. refsources/ + REVIEW_LOGs in the knowhow tree). `kind`
    stamps the retrieval tier on every Skill from this tree."""
    import os as _os
    if not root.is_dir():
        return []
    all_md: list[Path] = []
    for dp, _dn, fns in _os.walk(root, followlinks=True):
        for fn in fns:
            if fn.endswith(".md"):
                p = Path(dp) / fn
                if skip and skip(p):
                    continue
                all_md.append(p)
    all_md.sort()
    found: list[Skill] = []
    consumed: set[Path] = set()        # folder-skill dirs (internals aren't standalone)
    folder_names: set[str] = set()

    for skill_md in [f for f in all_md if f.name == "SKILL.md"]:
        folder = skill_md.parent
        parsed = _read_skill_fm(skill_md, scope, provenance)
        if parsed is None:
            continue
        fm, body = parsed
        name = _skill_canonical_name(skill_md, fm, is_folder=True)
        vis, dom = tier_of(skill_md.relative_to(root).parts, fm)
        found.append(Skill(name=name, path=skill_md, body=body, frontmatter=fm,
                           source_scope=scope.name, is_folder=True,
                           visibility=vis, domain=dom, kind=kind))
        consumed.add(folder)
        folder_names.add(name)

    for f in all_md:
        if f.name == "SKILL.md":
            continue
        if any(f.is_relative_to(d) for d in consumed):
            continue                    # a folder skill's internal .md (references/…)
        parsed = _read_skill_fm(f, scope, provenance)
        if parsed is None:
            continue
        fm, body = parsed
        name = _skill_canonical_name(f, fm, is_folder=False)
        if name in folder_names:
            continue
        vis, dom = tier_of(f.relative_to(root).parts, fm)
        found.append(Skill(name=name, path=f, body=body, frontmatter=fm,
                           source_scope=scope.name, is_folder=False,
                           visibility=vis, domain=dom, kind=kind))
    return found


def _discover_skills(scope: ScopeBundle, provenance: Provenance) -> list[Skill]:
    """Walk a scope's skills/ tree. Visibility + domain come from the tier subdir
    (_skill_tier). The recursive walk is what lets the system scope expose the
    tiered library (skills/core/, skills/recipes/<domain>/, skills/vendor_skills/
    <pkg>/) as well as a simple bundle's flat skills/<name>.md."""
    return _discover_skill_tree(
        scope.path / "skills", scope, provenance, kind="recipe",
        tier_of=lambda rel_parts, _fm: _skill_tier(rel_parts))


def _discover_knowhow(scope: ScopeBundle, provenance: Provenance) -> list[Skill]:
    """Walk a scope's knowhow/ tree as the ADVICE tier (broad decision guides).
    Every entry is retrieval-gated ('local') and tagged kind='knowhow'; domain
    comes from frontmatter. `knowhow/refsources/` (reference-source YAML, consumed
    by _compose_refsources) and REVIEW_LOG notes are NOT skills."""
    kdir = scope.path / "knowhow"

    def _skip(p: Path) -> bool:
        try:
            parts = p.relative_to(kdir).parts
        except ValueError:
            return True
        if parts and parts[0] == "refsources":
            return True
        return "REVIEW_LOG" in p.name

    def _tier(_rel_parts, fm) -> tuple[str, str]:
        return "local", str((fm or {}).get("domain") or "").strip()

    return _discover_skill_tree(kdir, scope, provenance, kind="knowhow",
                                tier_of=_tier, skip=_skip)


def _compose_skills(chain: list[ScopeBundle],
                     disabled: set[str],
                     provenance: Provenance) -> list[Skill]:
    """Override by skill name, narrowest-first; apply agents filter +
    disable_recipes."""
    seen: dict[str, Skill] = {}
    shadowed: dict[str, list[str]] = {}
    skipped: dict[str, str] = {}

    for s in reversed(chain):
        if not s.present:
            continue
        # skills/ (recipes) BEFORE knowhow/ so a same-named recipe wins the
        # in-scope collision (first-seen wins below) — e.g. a `bulk_rnaseq_de`
        # recipe shadows a knowhow draft of the same name.
        for skill in _discover_skills(s, provenance) + _discover_knowhow(s, provenance):
            # agents filter
            agents = skill.frontmatter.get("agents") if skill.frontmatter else None
            if isinstance(agents, list) and agents:
                if "aba" not in agents and "*" not in agents:
                    if skill.name not in seen and skill.name not in skipped:
                        skipped[skill.name] = f"agents: {agents!r}"
                    continue
            if skill.name in seen:
                shadowed.setdefault(skill.name, []).append(s.name)
                continue
            seen[skill.name] = skill

    # Apply disable_recipes
    out: list[Skill] = []
    for name, skill in seen.items():
        if name in disabled:
            provenance.skills[name] = {
                "effective_scope": None,
                "shadowed_in": shadowed.get(name, []),
                "disabled": True,
            }
            continue
        provenance.skills[name] = {
            "effective_scope": skill.source_scope,
            "shadowed_in": shadowed.get(name, []),
            "disabled": False,
        }
        out.append(skill)

    # Skipped (agents filter)
    for name, reason in skipped.items():
        provenance.skills[name] = {
            "effective_scope": None,
            "shadowed_in": [],
            "disabled": False,
            "skipped_reason": reason,
        }

    # Warn about disable_recipes that don't match anything
    for d in disabled:
        if d not in seen:
            provenance.warnings.append(
                f"disable_recipes: {d!r} not found in any scope")

    # Deterministic order: by name
    return sorted(out, key=lambda s: s.name)


def _read_yaml_safe(path: Path,
                     provenance: Provenance, scope_name: str) -> dict | None:
    """Read a YAML file, returning None if missing or malformed."""
    if not path.is_file():
        return None
    try:
        import yaml
        v = yaml.safe_load(path.read_text())
    except Exception as e:
        provenance.warnings.append(
            f"{scope_name}: malformed {path.name}: {e}")
        return None
    return v if isinstance(v, dict) else None


def _read_json_safe(path: Path,
                     provenance: Provenance, scope_name: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        import json
        v = json.loads(path.read_text())
    except Exception as e:
        provenance.warnings.append(
            f"{scope_name}: malformed {path.name}: {e}")
        return None
    return v if isinstance(v, dict) else None


def _dict_merge(base: dict, overlay: dict, *,
                  source_scope: str,
                  provenance: Provenance,
                  path_prefix: str = "") -> dict:
    """Deep dict-merge. Scalars: overlay wins (records source).
    Lists: extend (base + overlay). Dicts: recurse."""
    result = dict(base)
    for k, v in overlay.items():
        key_path = f"{path_prefix}.{k}" if path_prefix else k
        if k in result:
            if isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = _dict_merge(
                    result[k], v, source_scope=source_scope,
                    provenance=provenance, path_prefix=key_path,
                )
            elif isinstance(result[k], list) and isinstance(v, list):
                # Extend (default behavior)
                result[k] = result[k] + v
                provenance.settings_keys[key_path] = f"{provenance.settings_keys.get(key_path, '?')},{source_scope}"
            else:
                # Scalar overwrite (narrower wins because we're called
                # broadest→narrowest)
                result[k] = v
                provenance.settings_keys[key_path] = source_scope
        else:
            result[k] = v
            provenance.settings_keys[key_path] = source_scope
    return result


def _compose_settings(chain: list[ScopeBundle],
                       provenance: Provenance) -> dict:
    """Dict-merge settings.yaml across scopes; merge settings.json's
    overlap-keys (model, env) on top. Broadest first."""
    merged: dict = {}
    for s in chain:
        if not s.present:
            continue
        y = _read_yaml_safe(
            s.path / "settings.yaml", provenance, s.name)
        if y:
            merged = _dict_merge(
                merged, y, source_scope=s.name, provenance=provenance)
    # settings.json: only merge known overlapping keys
    for s in chain:
        if not s.present:
            continue
        j = _read_json_safe(
            s.path / "settings.json", provenance, s.name)
        if not j:
            continue
        # Only the overlap-keys ABA understands today
        overlay = {}
        if "model" in j:
            overlay["default_model"] = j["model"]
        if "env" in j and isinstance(j["env"], dict):
            overlay["env"] = j["env"]
        if overlay:
            merged = _dict_merge(
                merged, overlay,
                source_scope=f"{s.name}.settings.json",
                provenance=provenance,
            )
    return merged


def _compose_catalog(chain: list[ScopeBundle],
                     provenance: Provenance,
                     ) -> tuple[list[CatalogEntry], list[str], list[Path]]:
    """Compose each present scope's catalog/ dir into three projections:

      - capability specs, override by name (narrowest scope wins, like skills);
      - the curated R-base conda spec list, extended broadest→narrowest (deduped,
        order preserved) — labs add to the base, they don't replace it;
      - file-backed collection dirs (subdirs with a collection.yaml) to register
        for search (collections.md).

    Dispatch is by YAML content, not filename: a catalog/*.yaml with a
    `capabilities:` list contributes capability specs; one with a `packages:`
    list contributes R-base specs (a file may carry both). So the materialized
    system scope (bio_seed.yaml / r_base.yaml) and a future imported pack
    (python_bio.yaml / r_bioconductor.yaml) compose identically.
    """
    # Read every catalog/*.yaml once, grouped by scope (chain = broadest-first).
    scope_docs: list[tuple[ScopeBundle, list[dict]]] = []
    collection_dirs: list[Path] = []
    for s in chain:
        if not s.present:
            continue
        cat_dir = s.path / "catalog"
        if not cat_dir.is_dir():
            continue
        docs = [d for yf in sorted(cat_dir.glob("*.yaml"))
                if (d := _read_yaml_safe(yf, provenance, s.name))]
        scope_docs.append((s, docs))
        for sub in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
            if (sub / "collection.yaml").is_file():
                collection_dirs.append(sub.resolve())

    # Capabilities: narrowest-first so a lab/user entry overrides a system one
    # of the same name.
    seen: dict[str, CatalogEntry] = {}
    shadowed: dict[str, list[str]] = {}
    for s, docs in reversed(scope_docs):
        for doc in docs:
            for spec in (doc.get("capabilities") or []):
                if not isinstance(spec, dict):
                    continue
                name = spec.get("name")
                if not name:
                    continue
                if name in seen:
                    shadowed.setdefault(name, []).append(s.name)
                    continue
                seen[name] = CatalogEntry(name=name, spec=spec, source_scope=s.name)

    # R-base packages: broadest-first extend (dedup, preserve first occurrence).
    r_base: list[str] = []
    r_seen: set[str] = set()
    for s, docs in scope_docs:
        for doc in docs:
            for pkg in (doc.get("packages") or []):
                p = str(pkg)
                if p not in r_seen:
                    r_seen.add(p)
                    r_base.append(p)

    for name, entry in seen.items():
        provenance.capabilities[name] = {
            "effective_scope": entry.source_scope,
            "shadowed_in": shadowed.get(name, []),
        }

    return sorted(seen.values(), key=lambda c: c.name), r_base, collection_dirs


def _compose_refsources(chain: list[ScopeBundle],
                        provenance: Provenance) -> dict[str, dict]:
    """Compose each present scope's ``knowhow/refsources/*.yaml`` into one
    provider map — override by ``provider:`` name, narrowest scope wins (exactly
    like capabilities in `_compose_catalog`).

    This is the *data half* of fetch_reference: the platform seed is the system
    scope's floor, the recipe-pack / institution overlay extends or overrides
    providers, all without any layering logic in refsources.py — it just consumes
    ``EffectiveBundle.refsources``."""
    scope_docs: list[tuple[ScopeBundle, list[dict]]] = []
    for s in chain:
        if not s.present:
            continue
        rdir = s.path / "knowhow" / "refsources"
        if not rdir.is_dir():
            continue
        docs = [d for yf in sorted(rdir.glob("*.yaml"))
                if (d := _read_yaml_safe(yf, provenance, s.name))]
        if docs:
            scope_docs.append((s, docs))

    seen: dict[str, dict] = {}
    src: dict[str, str] = {}
    shadowed: dict[str, list[str]] = {}
    for s, docs in reversed(scope_docs):          # narrowest first
        for doc in docs:
            name = doc.get("provider")
            if not name:
                continue
            if name in seen:
                shadowed.setdefault(name, []).append(s.name)
                continue
            seen[name] = doc
            src[name] = s.name
    for name in seen:
        provenance.refsources[name] = {
            "effective_scope": src[name],
            "shadowed_in": shadowed.get(name, []),
        }
    return seen


# -----------------------------------------------------------------------
# Top-level entry
# -----------------------------------------------------------------------

def load_bundle(resolution: ScopeResolution) -> EffectiveBundle:
    """Compose all scopes in `resolution.scope_chain` into a single
    EffectiveBundle per the layering spec.

    The algorithm is scope-count-agnostic — works the same way for
    chains of length 1 (Mac dev) and length 4+ (lab cluster + future
    scopes)."""
    eb = EffectiveBundle()
    chain = resolution.scope_chain

    # 1. AGENTS.md / CLAUDE.md
    eb.policy_blocks = _compose_policy_blocks(chain, eb.provenance)

    # 2. rules/required/* (additive)
    eb.required_rules = _compose_required_rules(chain, eb.provenance)

    # 3. rules/* (loose, narrowest wins)
    eb.overrideable_rules = _compose_overrideable_rules(chain, eb.provenance)

    # 4. settings (dict-merge); we need disabled set BEFORE skills.
    eb.settings = _compose_settings(chain, eb.provenance)
    disabled: set[str] = set()
    for d in (eb.settings.get("disable_recipes") or []):
        if isinstance(d, str):
            disabled.add(d)

    # 5. skills (override + agents filter + disable_recipes)
    eb.skills = _compose_skills(chain, disabled, eb.provenance)

    # 6. catalog (capabilities override-by-name + curated R-base + collections)
    eb.catalog, eb.r_base_specs, eb.collection_dirs = _compose_catalog(
        chain, eb.provenance)

    # 7. refsources (provider manifests, override-by-provider-name like catalog)
    eb.refsources = _compose_refsources(chain, eb.provenance)

    # Carry resolver-side warnings through
    eb.provenance.warnings.extend(resolution.warnings)

    return eb


# -----------------------------------------------------------------------
# Pretty-printer
# -----------------------------------------------------------------------

def format_effective_bundle(eb: EffectiveBundle) -> str:
    """Compact human-readable summary."""
    lines = []
    lines.append(f"[bundle] policy: {len(eb.provenance.policy_scopes)} scope(s)")
    lines.append(f"[bundle] required rules: {len(eb.required_rules)} "
                  f"(from {sum(len(v) for v in eb.provenance.required_files.values())} files)")
    n_shadowed = sum(1 for v in eb.provenance.overrideable_files.values()
                      if v.get("shadowed_in"))
    lines.append(f"[bundle] overrideable rules: {len(eb.overrideable_rules)} "
                  f"({n_shadowed} shadowed)")
    n_disabled = sum(1 for v in eb.provenance.skills.values() if v.get("disabled"))
    n_skipped = sum(1 for v in eb.provenance.skills.values()
                     if v.get("skipped_reason"))
    lines.append(f"[bundle] skills: {len(eb.skills)} "
                  f"({n_disabled} disabled, {n_skipped} agent-filtered)")
    lines.append(f"[bundle] catalog: {len(eb.catalog)} capabilities, "
                  f"{len(eb.r_base_specs)} r-base pkgs, "
                  f"{len(eb.collection_dirs)} collection(s)")
    n_rs_shadow = sum(1 for v in eb.provenance.refsources.values() if v.get("shadowed_in"))
    lines.append(f"[bundle] refsources: {len(eb.refsources)} provider(s)"
                 + (f", {n_rs_shadow} overridden" if n_rs_shadow else ""))
    lines.append(f"[bundle] settings: {len(eb.settings)} top-level keys")
    if eb.provenance.warnings:
        lines.append(f"[bundle] warnings: {len(eb.provenance.warnings)}")
        for w in eb.provenance.warnings[:5]:
            lines.append(f"           {w}")
    return "\n".join(lines)
