"""Tests for the bundle loader (in-process composition).

Each test builds a scope-tree under tmp_path, calls scope_resolver +
load_bundle, asserts on the EffectiveBundle. The fixture builder
keeps tests self-contained — no committed fixture files to drift.

Cases per misc/bundle_layering.md:
  - Single-scope (Mac default)
  - Two scopes, no conflict
  - Two scopes, overrideable rule shadowed
  - rules/required/ additive across scopes (both included)
  - skills override + disable_recipes + agents filter
  - settings dict-merge: scalar narrowest-wins, list extend
  - malformed YAML in a scope (graceful skip)
  - @path imports in AGENTS.md
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.scope_resolver import resolve_scopes   # noqa: E402
from core.bundle.loader import load_bundle, EffectiveBundle  # noqa: E402


# -------------------------------------------------------------------
# Fixture builders
# -------------------------------------------------------------------

def _w(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"))
    return path


def _mk_system(root: Path,
                agents_md: str | None = "# System policy floor\n",
                rules: dict[str, str] | None = None,
                required: dict[str, str] | None = None,
                skills: dict[str, str] | None = None,
                settings_yaml: dict | None = None) -> Path:
    p = root / "system"
    p.mkdir(parents=True, exist_ok=True)
    if agents_md is not None:
        _w(p / "AGENTS.md", agents_md)
    for k, v in (rules or {}).items():
        _w(p / "rules" / k, v)
    for k, v in (required or {}).items():
        _w(p / "rules" / "required" / k, v)
    for k, v in (skills or {}).items():
        # flat skills go as <name>.md
        _w(p / "skills" / k, v)
    if settings_yaml is not None:
        import yaml
        _w(p / "settings.yaml", yaml.safe_dump(settings_yaml))
    return p


def _add_scope(root: Path, name: str, **kwargs) -> Path:
    p = root / name
    p.mkdir(parents=True, exist_ok=True)
    if kwargs.get("agents_md") is not None:
        _w(p / "AGENTS.md", kwargs["agents_md"])
    for k, v in (kwargs.get("rules") or {}).items():
        _w(p / "rules" / k, v)
    for k, v in (kwargs.get("required") or {}).items():
        _w(p / "rules" / "required" / k, v)
    for k, v in (kwargs.get("skills") or {}).items():
        _w(p / "skills" / k, v)
    if kwargs.get("settings_yaml") is not None:
        import yaml
        _w(p / "settings.yaml", yaml.safe_dump(kwargs["settings_yaml"]))
    if kwargs.get("settings_json") is not None:
        _w(p / "settings.json", json.dumps(kwargs["settings_json"]))
    return p


def _load_from_paths(root: Path, *, system: Path, institution: Path | None = None,
                       lab: Path | None = None, user: Path | None = None) -> EffectiveBundle:
    """Build an env that points the resolver at the given scope paths,
    then resolve + load."""
    env = {
        "HOME": str(root / "home"),
        "USER": "tester",
        "ABA_SYSTEM_BUNDLE": str(system),
    }
    if institution: env["ABA_INSTITUTION_BUNDLE"] = str(institution)
    if lab:         env["ABA_LAB_BUNDLE"] = str(lab); env["ABA_GROUP"] = "testlab"
    if user:        env["ABA_USER_BUNDLE"] = str(user)
    (root / "home").mkdir(exist_ok=True)
    r = resolve_scopes(env=env, site_config_path=None,
                        system_bundle_default=system, auto_create=True)
    return load_bundle(r)


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

def test_single_scope_no_header(tmp_path: Path):
    """Mac default — one scope. No section headers in the rendered
    policy (it would be silly noise)."""
    system = _mk_system(
        tmp_path,
        agents_md="# Built-in policy\n\nDo X, do Y.\n",
        rules={"figure_style.md": "Use viridis."},
        required={"plan_first.md": "Always plan first."},
        skills={"foo.md": "# foo skill\n"},
    )
    eb = _load_from_paths(tmp_path, system=system)

    # No "## System policy" header for single-scope
    assert "## System policy" not in eb.policy_text
    assert "Do X, do Y." in eb.policy_text
    assert len(eb.required_rules) == 1
    assert eb.required_rules[0].filename == "plan_first.md"
    assert len(eb.overrideable_rules) == 1
    assert eb.overrideable_rules[0].filename == "figure_style.md"
    assert len(eb.skills) == 1
    assert eb.skills[0].name == "foo"


def test_two_scope_policy_concatenates(tmp_path: Path):
    """When ≥2 scopes contribute AGENTS.md, the rendered policy has
    section headers naming each scope."""
    system = _mk_system(tmp_path, agents_md="System base.\n")
    inst = _add_scope(tmp_path, "institution", agents_md="Institution overlay.\n")

    eb = _load_from_paths(tmp_path, system=system, institution=inst)

    assert "## System policy" in eb.policy_text
    # Default institution label is "Institution" but can be overridden
    # by site config — here we get a sensible default.
    assert "Institution" in eb.policy_text
    assert "System base." in eb.policy_text
    assert "Institution overlay." in eb.policy_text
    assert eb.provenance.policy_scopes == ["system", "institution"]


def test_overrideable_rule_shadowed(tmp_path: Path):
    """Same-name file in two scopes: narrower wins; broader recorded
    as shadowed."""
    system = _mk_system(tmp_path,
                         rules={"figure_style.md": "# system style"})
    inst = _add_scope(tmp_path, "institution",
                       rules={"figure_style.md": "# institution style"})
    lab = _add_scope(tmp_path, "lab",
                      rules={"figure_style.md": "# lab style"})

    eb = _load_from_paths(tmp_path, system=system, institution=inst, lab=lab)

    rules_by_name = {r.filename: r for r in eb.overrideable_rules}
    assert "figure_style.md" in rules_by_name
    assert rules_by_name["figure_style.md"].source_scope == "lab"
    assert "lab style" in rules_by_name["figure_style.md"].content
    prov = eb.provenance.overrideable_files["figure_style.md"]
    assert prov["effective_scope"] == "lab"
    assert set(prov["shadowed_in"]) == {"institution", "system"}


def test_required_rules_additive(tmp_path: Path):
    """Required rules with the same name across scopes: ALL included."""
    system = _mk_system(tmp_path,
                         required={"doi.md": "system-required-doi"})
    inst = _add_scope(tmp_path, "institution",
                       required={"doi.md": "institution-required-doi"})

    eb = _load_from_paths(tmp_path, system=system, institution=inst)

    # Both files included
    assert len(eb.required_rules) == 2
    scopes_for_doi = [r.source_scope for r in eb.required_rules
                       if r.filename == "doi.md"]
    assert scopes_for_doi == ["system", "institution"]
    assert eb.provenance.required_files["doi.md"] == ["system", "institution"]


def test_skill_override_by_name(tmp_path: Path):
    """Same-name skill in narrower scope wins; broader shadowed."""
    system = _mk_system(
        tmp_path,
        skills={"qc.md": "---\nname: qc\n---\n# system version\n"},
    )
    inst = _add_scope(tmp_path, "institution",
                       skills={"qc.md": "---\nname: qc\n---\n# inst version\n"})

    eb = _load_from_paths(tmp_path, system=system, institution=inst)

    by_name = {s.name: s for s in eb.skills}
    assert "qc" in by_name
    assert by_name["qc"].source_scope == "institution"
    assert "inst version" in by_name["qc"].body
    assert eb.provenance.skills["qc"]["effective_scope"] == "institution"
    assert eb.provenance.skills["qc"]["shadowed_in"] == ["system"]


def test_skill_disabled_by_settings(tmp_path: Path):
    """disable_recipes in any scope drops the skill from the catalog."""
    system = _mk_system(
        tmp_path,
        skills={"keeper.md": "---\nname: keeper\n---\n# keep me\n",
                "legacy.md": "---\nname: legacy\n---\n# old\n"},
    )
    inst = _add_scope(tmp_path, "institution",
                       settings_yaml={"disable_recipes": ["legacy"]})

    eb = _load_from_paths(tmp_path, system=system, institution=inst)

    names = {s.name for s in eb.skills}
    assert names == {"keeper"}
    assert eb.provenance.skills["legacy"]["disabled"] is True


def test_disable_recipes_unknown_warns(tmp_path: Path):
    """disable_recipes referencing a recipe that doesn't exist → warn."""
    system = _mk_system(
        tmp_path,
        skills={"keeper.md": "---\nname: keeper\n---\n# keep me\n"},
        settings_yaml={"disable_recipes": ["typo_name"]},
    )
    eb = _load_from_paths(tmp_path, system=system)
    assert any("typo_name" in w for w in eb.provenance.warnings)


def test_skill_agents_filter(tmp_path: Path):
    """agents: [openclaw] excludes the skill from ABA's catalog."""
    system = _mk_system(
        tmp_path,
        skills={
            "aba_only.md": "---\nname: aba_only\nagents: [aba]\n---\n# for aba\n",
            "openclaw_only.md": "---\nname: openclaw_only\nagents: [openclaw]\n---\n# nope\n",
            "universal.md": "---\nname: universal\n---\n# everyone\n",
        },
    )
    eb = _load_from_paths(tmp_path, system=system)
    names = {s.name for s in eb.skills}
    assert names == {"aba_only", "universal"}
    assert eb.provenance.skills["openclaw_only"]["skipped_reason"]


def test_folder_skill_discovery(tmp_path: Path):
    """SKILL.md inside a subdirectory is discovered + treated as a
    folder skill."""
    system = tmp_path / "system"
    skill_dir = system / "skills" / "fancy"
    skill_dir.mkdir(parents=True)
    _w(skill_dir / "SKILL.md",
        "---\nname: fancy\n---\n# folder skill\n")

    eb = _load_from_paths(tmp_path, system=system)
    by_name = {s.name: s for s in eb.skills}
    assert "fancy" in by_name
    assert by_name["fancy"].is_folder


def test_settings_dict_merge(tmp_path: Path):
    """Scalars: narrowest wins. Lists: extend (broadest-first)."""
    system = _mk_system(
        tmp_path,
        settings_yaml={
            "default_model": "claude-haiku-4-5",
            "default_data_paths": ["/system/data"],
            "advisor_model": "claude-haiku-4-5",
        },
    )
    inst = _add_scope(tmp_path, "institution", settings_yaml={
        "default_model": "claude-sonnet-4-6",   # overrides system
        "default_data_paths": ["/cluster/atlases"],   # extends
    })
    lab = _add_scope(tmp_path, "lab", settings_yaml={
        "default_model": "claude-opus-4-7",     # overrides both
        "default_data_paths": ["/groups/lab/data"],   # extends further
    })

    eb = _load_from_paths(tmp_path, system=system, institution=inst, lab=lab)
    s = eb.settings
    assert s["default_model"] == "claude-opus-4-7"
    assert s["advisor_model"] == "claude-haiku-4-5"   # only system set it
    # lists extend broadest-first
    assert s["default_data_paths"] == [
        "/system/data", "/cluster/atlases", "/groups/lab/data"]


def test_settings_json_overlap(tmp_path: Path):
    """settings.json's model key is mapped into default_model overlap."""
    system = _mk_system(tmp_path)
    inst = _add_scope(tmp_path, "institution", settings_json={
        "model": "claude-sonnet-4-6",
        "env": {"FOO": "bar"},
        "unknown_key": "ignored",
    })
    eb = _load_from_paths(tmp_path, system=system, institution=inst)
    assert eb.settings["default_model"] == "claude-sonnet-4-6"
    assert eb.settings["env"] == {"FOO": "bar"}
    # unknown_key is NOT merged (we only carry the documented overlap)
    assert "unknown_key" not in eb.settings


def test_malformed_settings_does_not_crash(tmp_path: Path):
    """Broken YAML in a scope → that scope's settings skipped, warning
    recorded, composition continues."""
    system = _mk_system(tmp_path,
                         settings_yaml={"default_model": "haiku"})
    inst = tmp_path / "institution"
    inst.mkdir()
    _w(inst / "settings.yaml", ":\n  not valid yaml: [\n")

    eb = _load_from_paths(tmp_path, system=system, institution=inst)
    # System's settings survived; institution's malformed file produced
    # a warning but didn't crash.
    assert eb.settings["default_model"] == "haiku"
    assert any("malformed" in w for w in eb.provenance.warnings)


def test_at_path_import_resolves(tmp_path: Path):
    """@path imports in AGENTS.md are inlined recursively."""
    system = tmp_path / "system"
    _w(system / "AGENTS.md", "Header.\n@included.md\nFooter.\n")
    _w(system / "included.md", "INCLUDED CONTENT\n")
    eb = _load_from_paths(tmp_path, system=system)
    assert "INCLUDED CONTENT" in eb.policy_text
    assert "Header." in eb.policy_text
    assert "Footer." in eb.policy_text


def test_claude_md_fallback(tmp_path: Path):
    """If a scope has CLAUDE.md but no AGENTS.md, CLAUDE.md is read."""
    system = tmp_path / "system"
    system.mkdir()
    _w(system / "CLAUDE.md", "# fallback policy via CLAUDE.md\n")
    eb = _load_from_paths(tmp_path, system=system)
    assert "fallback policy via CLAUDE.md" in eb.policy_text


def test_scope_order_invariant_three_scope_cascade(tmp_path: Path):
    """End-to-end: system + institution + lab with mixed overrides.
    Verify each piece ends up sourced from the expected scope."""
    system = _mk_system(
        tmp_path,
        agents_md="System base.\n",
        rules={"figure_style.md": "system figs",
               "notetaking.md": "system notes"},
        required={"plan_first.md": "Plan first"},
        skills={"qc.md": "---\nname: qc\n---\nsys qc\n",
                "cluster.md": "---\nname: cluster\n---\nsys cluster\n"},
        settings_yaml={"default_model": "haiku",
                       "default_data_paths": ["/sys/data"]},
    )
    inst = _add_scope(tmp_path, "institution",
        agents_md="Institution overlay.\n",
        required={"doi.md": "DOI compliance"},
        rules={"figure_style.md": "inst figs",    # overrides system
               "notetaking.md": "inst notes"},     # overrides system
        skills={"qc.md": "---\nname: qc\n---\ninst qc\n"},  # overrides system qc
        settings_yaml={"default_model": "sonnet",
                       "default_data_paths": ["/cluster/atlases"]},
    )
    lab = _add_scope(tmp_path, "lab",
        agents_md="Lab voice.\n",
        rules={"figure_style.md": "lab figs"},     # overrides both above
        skills={"lab_de.md": "---\nname: lab_de\n---\nlab DE\n"},  # NEW
        settings_yaml={"default_data_paths": ["/groups/lab/data"]},  # extends
    )
    eb = _load_from_paths(tmp_path, system=system, institution=inst, lab=lab)

    # Policy: three sections
    assert "## System policy" in eb.policy_text
    # Required: only DOI from institution (system has plan_first too)
    req_names = sorted(r.filename for r in eb.required_rules)
    assert req_names == ["doi.md", "plan_first.md"]
    # Overrideable: figure_style from lab, notetaking from institution
    by_name = {r.filename: r for r in eb.overrideable_rules}
    assert by_name["figure_style.md"].source_scope == "lab"
    assert by_name["notetaking.md"].source_scope == "institution"
    # Skills: qc from institution (overrides system), cluster from system, lab_de new
    skill_scopes = {s.name: s.source_scope for s in eb.skills}
    assert skill_scopes == {"qc": "institution",
                              "cluster": "system",
                              "lab_de": "lab"}
    # Settings: default_model = sonnet (institution wins, lab didn't set);
    # data_paths extends all three.
    assert eb.settings["default_model"] == "sonnet"
    assert eb.settings["default_data_paths"] == [
        "/sys/data", "/cluster/atlases", "/groups/lab/data",
    ]


def test_no_present_scopes_returns_empty(tmp_path: Path):
    """If the configured system bundle path doesn't exist (and no other
    scopes), the loader returns an empty EffectiveBundle (no crash)."""
    missing = tmp_path / "does-not-exist"
    env = {"HOME": str(tmp_path / "home"), "USER": "tester",
           "ABA_SYSTEM_BUNDLE": str(missing)}
    (tmp_path / "home").mkdir()
    r = resolve_scopes(env=env, site_config_path=None,
                        system_bundle_default=missing, auto_create=False)
    eb = load_bundle(r)
    assert eb.policy_text == ""
    assert eb.required_rules == []
    assert eb.overrideable_rules == []
    assert eb.skills == []
    # The "system bundle not found" warning propagated from resolver.
    assert any("not found" in w for w in eb.provenance.warnings)
