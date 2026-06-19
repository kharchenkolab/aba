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


@dataclass
class Provenance:
    """Records of what each scope contributed + what was shadowed."""
    policy_scopes: list[str] = field(default_factory=list)
    required_files: dict[str, list[str]] = field(default_factory=dict)
    overrideable_files: dict[str, dict] = field(default_factory=dict)
    skills: dict[str, dict] = field(default_factory=dict)
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
    no frontmatter is present."""
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


def _discover_skills(scope: ScopeBundle, provenance: Provenance) -> list[Skill]:
    """Walk a scope's skills/ dir. Returns both folder and flat skills."""
    skills_dir = scope.path / "skills"
    if not skills_dir.is_dir():
        return []
    found: list[Skill] = []

    # Folder skills first (each subdir with SKILL.md)
    seen_folder_names: set[str] = set()
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.is_file():
            continue
        try:
            text = skill_md.read_text()
        except Exception as e:
            provenance.warnings.append(
                f"{scope.name}: unreadable {skill_md}: {e}")
            continue
        fm, body = _parse_frontmatter(text)
        name = _skill_canonical_name(skill_md, fm, is_folder=True)
        seen_folder_names.add(name)
        found.append(Skill(
            name=name, path=skill_md, body=body,
            frontmatter=fm, source_scope=scope.name, is_folder=True,
        ))

    # Flat skills (top-level .md files; skip if name collides with a
    # folder skill that already won)
    for f in sorted(skills_dir.glob("*.md")):
        try:
            text = f.read_text()
        except Exception as e:
            provenance.warnings.append(
                f"{scope.name}: unreadable {f}: {e}")
            continue
        fm, body = _parse_frontmatter(text)
        name = _skill_canonical_name(f, fm, is_folder=False)
        if name in seen_folder_names:
            continue
        found.append(Skill(
            name=name, path=f, body=body,
            frontmatter=fm, source_scope=scope.name, is_folder=False,
        ))
    return found


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
        for skill in _discover_skills(s, provenance):
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
    lines.append(f"[bundle] settings: {len(eb.settings)} top-level keys")
    if eb.provenance.warnings:
        lines.append(f"[bundle] warnings: {len(eb.provenance.warnings)}")
        for w in eb.provenance.warnings[:5]:
            lines.append(f"           {w}")
    return "\n".join(lines)
