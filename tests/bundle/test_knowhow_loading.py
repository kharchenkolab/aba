"""Knowhow tier loading — the bundle loader must expose each scope's
`knowhow/` tree as searchable skills (kind='knowhow', visibility='local'),
NOT just `knowhow/refsources/*.yaml`.

Regression guard for the gap where ~150 KB of drafted scRNA decision guides in
aba-recipe-pack/knowhow/ reached neither the skills registry nor any cross-link
(0 recipes referenced them, no fetch tool). The loader only walked `skills/`.

Asserts:
  - flat knowhow (<name>.md) + folder knowhow (<dir>/SKILL.md) both load
  - they are kind='knowhow', visibility='local', with body + frontmatter domain
  - knowhow/refsources/*.yaml is NOT a skill (it's reference-source data)
  - REVIEW_LOG notes (flat and inside a folder skill) are NOT skills
  - a skills/ recipe SHADOWS a same-named knowhow/ draft (recipe wins, kind stays 'recipe')
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.scope_resolver import resolve_scopes   # noqa: E402
from core.bundle.loader import load_bundle               # noqa: E402


def _w(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip("\n"))
    return path


def _build(tmp_path: Path):
    sysroot = tmp_path / "system"
    # --- skills/ tree (recipe tier) ---
    _w(sysroot / "skills" / "core" / "op.md", "---\nname: op\n---\n# core op\n")
    _w(sysroot / "skills" / "recipes" / "genomics" / "bp-annotation.md",
       "---\nname: bp-annotation\ndomain: genomics\n---\n# annotate (recipe)\n")
    # collision: a recipe named bulk_rnaseq_de exists in skills/ too
    _w(sysroot / "skills" / "recipes" / "genomics" / "bulk_rnaseq_de.md",
       "---\nname: bulk_rnaseq_de\ndomain: genomics\n---\n# bulk DE RECIPE body\n")
    # --- knowhow/ tree (advice tier) ---
    _w(sysroot / "knowhow" / "scrna-integration-decision.md",
       "---\nname: scrna-integration-decision\ndomain: genomics\n"
       "kind: knowhow_draft\nwhen_to_use: choosing an integration method\n---\n"
       "# which integration method KNOWHOW body\n")
    _w(sysroot / "knowhow" / "scrna-de-methodology" / "SKILL.md",
       "---\nname: scrna-de-methodology\ndomain: genomics\nkind: knowhow_draft\n---\n"
       "# DE methodology KNOWHOW body\n")
    # collision draft — must lose to the skills/ recipe of the same name
    _w(sysroot / "knowhow" / "bulk_rnaseq_de.md",
       "---\nname: bulk_rnaseq_de\ndomain: genomics\nkind: knowhow_draft\n---\n"
       "# bulk DE KNOWHOW body (should be shadowed)\n")
    # must NOT become skills:
    _w(sysroot / "knowhow" / "refsources" / "ensembl.yaml", "provider: ensembl\n")
    _w(sysroot / "knowhow" / "scrna-analysis.REVIEW_LOG.md", "# review notes\n")
    _w(sysroot / "knowhow" / "scrna-de-methodology" / "REVIEW_LOG.md", "# folder review notes\n")

    env = {"HOME": str(tmp_path / "home"), "USER": "tester",
           "ABA_SYSTEM_BUNDLE": str(sysroot)}
    (tmp_path / "home").mkdir(exist_ok=True)
    r = resolve_scopes(env=env, site_config_path=None,
                       system_bundle_default=sysroot, auto_create=True)
    return load_bundle(r)


def test_knowhow_files_load_as_local_knowhow_skills(tmp_path: Path):
    eb = _build(tmp_path)
    by_name = {s.name: s for s in eb.skills}

    # flat knowhow
    assert "scrna-integration-decision" in by_name, "flat knowhow not loaded"
    k = by_name["scrna-integration-decision"]
    assert k.kind == "knowhow"
    assert k.visibility == "local"
    assert k.domain == "genomics"
    assert "KNOWHOW body" in k.body

    # folder knowhow
    assert "scrna-de-methodology" in by_name, "folder knowhow not loaded"
    assert by_name["scrna-de-methodology"].kind == "knowhow"
    assert by_name["scrna-de-methodology"].is_folder is True


def test_refsources_and_review_logs_are_not_skills(tmp_path: Path):
    eb = _build(tmp_path)
    names = {s.name for s in eb.skills}
    # refsources yaml is reference data, never a skill
    assert not any("ensembl" in n for n in names)
    # REVIEW_LOG notes (flat + inside a folder skill) are not skills
    assert not any("REVIEW_LOG" in n or "review" in n.lower() for n in names)


def test_recipe_shadows_same_named_knowhow_draft(tmp_path: Path):
    eb = _build(tmp_path)
    by_name = {s.name: s for s in eb.skills}
    assert "bulk_rnaseq_de" in by_name
    s = by_name["bulk_rnaseq_de"]
    assert s.kind == "recipe", "skills/ recipe must win the name collision"
    assert "RECIPE body" in s.body
    assert "shadowed" not in s.body


def test_plain_recipes_stay_kind_recipe(tmp_path: Path):
    eb = _build(tmp_path)
    by_name = {s.name: s for s in eb.skills}
    assert by_name["bp-annotation"].kind == "recipe"
    assert by_name["op"].kind == "recipe"
    assert by_name["op"].visibility == "always"     # core tier unaffected


def test_malformed_frontmatter_is_flagged_not_silently_dropped(tmp_path: Path):
    """A knowhow with an unquoted ':' in a value (broken YAML) must NOT vanish
    silently — the loader records a LOUD provenance warning. Guards the exact
    trap that hid scrna-integration-decision / scrna-de-methodology behind a
    misleading 'missing name' skip."""
    sysroot = tmp_path / "system"
    _w(sysroot / "skills" / "core" / "op.md", "---\nname: op\n---\n# op\n")
    # unquoted 'directly: foo' → YAML 'mapping values are not allowed here'
    _w(sysroot / "knowhow" / "broken.md",
       "---\nname: broken\nwhen_to_use: route to the recipe directly: foo-bar\n---\n# body\n")

    env = {"HOME": str(tmp_path / "home"), "USER": "tester",
           "ABA_SYSTEM_BUNDLE": str(sysroot)}
    (tmp_path / "home").mkdir(exist_ok=True)
    r = resolve_scopes(env=env, site_config_path=None,
                       system_bundle_default=sysroot, auto_create=True)
    eb = load_bundle(r)

    names = {s.name for s in eb.skills}
    assert "broken" not in names, "malformed skill should not load"
    assert any("malformed" in w and "broken.md" in w for w in eb.provenance.warnings), \
        f"no loud warning for the dropped skill; warnings={eb.provenance.warnings}"


def test_spec_projection_threads_kind():
    """_spec_from_parsed must carry kind through to the SkillSpec (frontmatter
    kind: is ignored — the tier is directory-derived)."""
    from core.skills.loader import _spec_from_parsed
    spec = _spec_from_parsed(
        {"name": "x", "kind": "knowhow_draft"}, "body",
        visibility="local", kind="knowhow")
    assert spec.kind == "knowhow"


if __name__ == "__main__":
    import tempfile
    for fn in (test_knowhow_files_load_as_local_knowhow_skills,
               test_refsources_and_review_logs_are_not_skills,
               test_recipe_shadows_same_named_knowhow_draft,
               test_plain_recipes_stay_kind_recipe,
               test_malformed_frontmatter_is_flagged_not_silently_dropped):
        with tempfile.TemporaryDirectory() as d:
            fn(Path(d))
        print("PASS", fn.__name__)
    test_spec_projection_threads_kind()
    print("PASS test_spec_projection_threads_kind")
