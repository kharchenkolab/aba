"""P1 Stage 1 — the live skill catalog is a PROJECTION of EffectiveBundle.skills,
driven by the bundle scope chain (no content-layers / deployment.yaml).

Covers: lab/group skills surfacing with the right tier visibility + recipe
domain, narrowest-wins override of a system skill, disable_recipes dropping an
inherited skill, and the agents: filter excluding non-aba skills.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def _skill_md(name: str, desc: str = "d", **fm) -> str:
    extra = "".join(f"{k}: {v}\n" for k, v in fm.items())
    return f"---\nname: {name}\ndescription: {desc}\n{extra}---\n# {name} body\n"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Build a temp lab bundle, resolve system+lab, and project into the live
    catalog. Returns _run(lab_skills: {relpath: content}, settings=""). Restores
    the system-only catalog afterwards so tests don't leak into each other."""
    def _run(lab_skills: dict, settings: str = ""):
        lab = tmp_path / "lab"
        (lab / "skills").mkdir(parents=True)
        (lab / "AGENTS.md").write_text("Lab policy\n")
        for rel, content in lab_skills.items():
            f = lab / "skills" / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        if settings:
            (lab / "settings.yaml").write_text(settings)
        monkeypatch.setenv("ABA_LAB_BUNDLE", str(lab))
        monkeypatch.setenv("ABA_GROUP", "testlab")
        from core.bundle.active import reload_bundle
        import content.bio.skills as cbs
        reload_bundle()
        cbs.register_from_bundle(clear=True)
        return cbs
    yield _run
    from core.bundle.active import reload_bundle
    import content.bio.skills as cbs
    reload_bundle()
    cbs.register_from_bundle(clear=True)


def test_flat_lab_skill_is_local(project):
    project({"lab_qc.md": _skill_md("lab-qc", "lab QC")})
    from core.skills.loader import get_skill
    s = get_skill("lab-qc")
    assert s and s.layer == "lab" and s.visibility == "local"


def test_lab_core_skill_is_always(project):
    project({"core/lab_core.md": _skill_md("lab-core")})
    from core.skills.loader import get_skill
    s = get_skill("lab-core")
    assert s and s.layer == "lab" and s.visibility == "always"


def test_lab_recipe_carries_domain(project):
    project({"recipes/genomics/lab_gx.md": _skill_md("lab-gx")})
    from core.skills.loader import get_skill
    s = get_skill("lab-gx")
    assert s and s.domain == "genomics" and s.visibility == "local"


def test_lab_overrides_system_by_name(project):
    project({"core/manage-entities.md": _skill_md("manage-entities", "LAB VERSION")})
    from core.skills.loader import get_skill
    s = get_skill("manage-entities")
    assert s.layer == "lab" and s.description == "LAB VERSION"


def test_disable_recipes_drops_inherited(project):
    project({"lab_qc.md": _skill_md("lab-qc")},
            settings="disable_recipes: [lstar]\n")
    from core.skills.loader import get_skill
    assert get_skill("lstar") is None          # system skill disabled by the lab
    assert get_skill("lab-qc") is not None


def test_lab_alias_hijacks_system_name(project):
    # A lab recipe declares aliases:[<system skill name>]; lookups for that base
    # name resolve to the lab spec (alias-override, broadest→narrowest order).
    project({"recipes/x/vienna_me.md": _skill_md("vienna-manage", aliases="[manage-entities]")})
    from core.skills.loader import get_skill
    s = get_skill("manage-entities")
    assert s is not None and s.name == "vienna-manage" and s.layer == "lab"


def test_agents_filter_excludes_non_aba(project):
    project({"other.md": _skill_md("other-agent", agents="[claude-code]")})
    from core.skills.loader import get_skill
    assert get_skill("other-agent") is None


def test_installation_scope_recipes(tmp_path, monkeypatch):
    """Stage 2: the always-present installation ('institution') scope carries the
    imported recipe pack — recipes surface with layer='institution'."""
    inst = tmp_path / "installation"
    (inst / "skills" / "recipes" / "genomics").mkdir(parents=True)
    (inst / "skills" / "recipes" / "genomics" / "bulk_de.md").write_text(
        _skill_md("inst-bulk-de", "institution bulk RNA-seq DE"))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(inst))
    from core.bundle.active import reload_bundle
    import content.bio.skills as cbs
    from core.skills.loader import get_skill
    reload_bundle(); cbs.register_from_bundle(clear=True)
    try:
        s = get_skill("inst-bulk-de")
        assert s and s.layer == "institution"
        assert s.domain == "genomics" and s.visibility == "local"
    finally:
        monkeypatch.delenv("ABA_INSTITUTION_BUNDLE", raising=False)
        reload_bundle(); cbs.register_from_bundle(clear=True)


def test_empty_lab_leaves_system_only(project):
    project({})
    from core.skills.loader import list_skills
    sk = list_skills()
    assert "manage-entities" in {s.name for s in sk}   # a system core skill
    assert all(s.layer == "system" for s in sk)
