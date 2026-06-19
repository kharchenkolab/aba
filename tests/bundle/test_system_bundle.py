"""Regression test for the system bundle (backend/system_bundle/).

P4 created backend/system_bundle/ as a symlink-only tree pointing at
the existing content/bio/prompts/ + content/bio/library/ files. The
bundle loader should compose it cleanly with:
  - non-empty policy text (from AGENTS.md → identity.md)
  - required rules including plan_first.md
  - overrideable rules including behavior.md, figures.md
  - skills including lstar + pagoda2 (folder skills) + core skills
  - settings.yaml with default_model + advisor_model

This test guards against accidentally breaking the system bundle's
discoverability — e.g. by removing a symlink during cleanup.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.scope_resolver import resolve_scopes   # noqa: E402
from core.bundle.loader import load_bundle               # noqa: E402

SYSTEM_BUNDLE = ROOT / "backend" / "system_bundle"


def test_system_bundle_present():
    """The system bundle exists on disk at the documented path."""
    assert SYSTEM_BUNDLE.is_dir(), \
        f"Expected backend/system_bundle/ at {SYSTEM_BUNDLE}"


def test_system_bundle_has_agents_md():
    """AGENTS.md resolves (symlink target exists)."""
    agents = SYSTEM_BUNDLE / "AGENTS.md"
    assert agents.exists(), "system_bundle/AGENTS.md missing"
    assert agents.read_text().strip(), "system_bundle/AGENTS.md is empty"


def test_system_bundle_has_required_rules():
    """rules/required/ contains plan_first.md + nonnegotiables.md."""
    req = SYSTEM_BUNDLE / "rules" / "required"
    assert req.is_dir()
    names = {f.name for f in req.glob("*.md")}
    assert "plan_first.md" in names
    assert "nonnegotiables.md" in names


def test_system_bundle_has_overrideable_rules():
    """The behavioral rule blocks symlink in."""
    rules = SYSTEM_BUNDLE / "rules"
    files = {f.name for f in rules.glob("*.md")}
    for expected in ("behavior.md", "figures.md", "highlighting.md",
                     "recipes.md", "scenarios.md"):
        assert expected in files, f"missing rules/{expected}"


def test_system_bundle_has_settings_yaml():
    """settings.yaml present with model defaults."""
    s = SYSTEM_BUNDLE / "settings.yaml"
    assert s.is_file()
    import yaml
    data = yaml.safe_load(s.read_text())
    assert "default_model" in data
    assert "advisor_model" in data


def test_system_bundle_skills_are_discoverable():
    """Skills dir has both flat skills (core) + folder skills
    (vendor)."""
    skills = SYSTEM_BUNDLE / "skills"
    assert skills.is_dir()
    # Flat skills (core)
    flat = list(skills.glob("*.md"))
    assert len(flat) >= 5, f"expected ≥5 core flat skills, got {len(flat)}"
    # Folder skills (vendor)
    folder_skills = [d for d in skills.iterdir()
                     if d.is_dir() and (d / "SKILL.md").exists()]
    assert len(folder_skills) >= 1, "no folder skills found"


def test_loader_composes_system_bundle():
    """End-to-end: resolver + loader produce a useful EffectiveBundle
    when pointed at the actual system bundle."""
    r = resolve_scopes(
        env={"HOME": "/tmp", "USER": "tester",
             "ABA_SYSTEM_BUNDLE": str(SYSTEM_BUNDLE)},
        site_config_path=None,
        auto_create=False,
    )
    # The system scope must be present.
    sys_scope = next(s for s in r.scope_chain if s.name == "system")
    assert sys_scope.present, "system bundle not detected as present"

    eb = load_bundle(r)
    # Substantive content present.
    assert len(eb.policy_text) > 0, "policy_text empty"
    assert "Guide" in eb.policy_text or "agent" in eb.policy_text.lower()
    # Required rules.
    req_names = {r.filename for r in eb.required_rules}
    assert "plan_first.md" in req_names
    # Overrideable rules.
    ov_names = {r.filename for r in eb.overrideable_rules}
    assert "behavior.md" in ov_names
    # Skills.
    skill_names = {s.name for s in eb.skills}
    assert len(skill_names) >= 5
    # Settings.
    assert "default_model" in eb.settings


def test_claude_md_bridge_present():
    """CLAUDE.md bridge symlink so CC consumers see the bundle."""
    c = SYSTEM_BUNDLE / "CLAUDE.md"
    assert c.exists(), "CLAUDE.md bridge symlink missing"


def test_dot_claude_self_symlink_present():
    """.claude → . self-link, so .claude/skills resolves to ./skills/."""
    dotclaude = SYSTEM_BUNDLE / ".claude"
    assert dotclaude.is_symlink(), ".claude self-link missing"
    # And it resolves to the bundle itself
    assert dotclaude.resolve() == SYSTEM_BUNDLE.resolve()
    # And nested resolution works
    assert (dotclaude / "AGENTS.md").exists()
    assert (dotclaude / "skills").is_dir()
