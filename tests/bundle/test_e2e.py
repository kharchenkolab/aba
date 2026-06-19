"""P8: end-to-end validation of the bundle layering pipeline.

Exercises the full stack — scope resolver → bundle loader →
EffectiveBundle → cached accessor → rendered prompt → inspect output —
against a hand-rolled 4-scope fixture (system / institution / lab /
user) so the layering decisions documented in misc/bundle_layering.md
all light up at once.

The fixture deliberately covers the tricky cases:
  - Each scope contributes a unique AGENTS.md marker (chain order test).
  - Each scope ships a same-name required rule (additive test).
  - The lab scope shadows an overrideable rule originally in system.
  - The user scope overrides a skill by name.
  - The institution scope ships a skill with `agents: [other-agent]` so
    the filter drops it.
  - settings.yaml disables one of the institution-shipped recipes via
    `disable_recipes:`.
  - Lab settings.yaml extends a list and overrides a scalar.

The composer-mode / direct parity sub-tests from the plan are dropped:
the external composer was deferred — internal composition only.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle import active as bundle_active          # noqa: E402
from core.bundle.loader import load_bundle               # noqa: E402
from core.bundle.scope_resolver import resolve_scopes    # noqa: E402
from core.bundle.cli import _state_dict                  # noqa: E402


# -------------------------------------------------------------------
# Fixture builders
# -------------------------------------------------------------------

def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).lstrip("\n"))


def _build_system(p: Path) -> Path:
    """Minimal system scope: AGENTS.md + plan_first.md + behavior.md +
    one skill + settings.yaml with default_model + advisor_model."""
    _w(p / "AGENTS.md", """
        # ABA system identity
        SYS-IDENTITY-MARKER
    """)
    _w(p / "rules" / "required" / "plan_first.md", """
        # plan-first (system)
        SYS-PLAN-FIRST-MARKER
    """)
    _w(p / "rules" / "required" / "common.md", """
        # common required
        SYS-COMMON-REQUIRED
    """)
    _w(p / "rules" / "behavior.md", """
        # behavior (system version)
        SYS-BEHAVIOR-VERSION
    """)
    _w(p / "rules" / "figures.md", """
        # figures (system version)
        SYS-FIGURES-VERSION
    """)
    _w(p / "skills" / "qc.md", """
        ---
        name: qc
        description: Per-cell QC.
        ---
        SYS-QC-BODY
    """)
    _w(p / "settings.yaml", """
        default_model: claude-opus-system
        advisor_model: claude-haiku-system
        env:
          ABA_FROM_SYSTEM: "1"
    """)
    return p


def _build_institution(p: Path) -> Path:
    """Institution scope: extra AGENTS.md content + 2 institution skills
    (one of which is agents-filtered to a non-ABA agent) + a same-name
    required rule. settings.yaml disables one institution-shipped recipe."""
    _w(p / "AGENTS.md", """
        # institution policy
        INST-POLICY-MARKER
    """)
    _w(p / "rules" / "required" / "common.md", """
        # common required (institution version)
        INST-COMMON-REQUIRED
    """)
    _w(p / "skills" / "deseq2.md", """
        ---
        name: deseq2
        description: Bulk DE with DESeq2.
        ---
        INST-DESEQ2-BODY
    """)
    _w(p / "skills" / "private-internal.md", """
        ---
        name: private-internal
        description: Internal-only tooling.
        agents: ["other-agent"]
        ---
        INST-PRIVATE-BODY
    """)
    _w(p / "skills" / "soon-disabled.md", """
        ---
        name: soon-disabled
        description: Recipe disabled via institution settings.
        ---
        INST-SOON-DISABLED-BODY
    """)
    _w(p / "settings.yaml", """
        disable_recipes:
          - soon-disabled
        env:
          ABA_FROM_INST: "1"
    """)
    return p


def _build_lab(p: Path) -> Path:
    """Lab scope: extra AGENTS.md content + overrideable rule shadowing
    + list-extend in settings.yaml + a lab skill."""
    _w(p / "AGENTS.md", """
        # lab policy
        LAB-POLICY-MARKER
    """)
    _w(p / "rules" / "behavior.md", """
        # behavior (lab override)
        LAB-BEHAVIOR-VERSION
    """)
    _w(p / "skills" / "pagoda2.md", """
        ---
        name: pagoda2
        description: Lab-preferred clustering.
        ---
        LAB-PAGODA2-BODY
    """)
    _w(p / "settings.yaml", """
        default_model: claude-opus-lab
        env:
          ABA_FROM_LAB: "1"
        focus_tags:
          - lab-preset-1
          - lab-preset-2
    """)
    return p


def _build_user(p: Path) -> Path:
    """User scope: personal AGENTS.md + override of the qc skill (narrowest
    wins) + a personal preference."""
    _w(p / "AGENTS.md", """
        # user preferences
        USER-PREFS-MARKER
    """)
    _w(p / "skills" / "qc.md", """
        ---
        name: qc
        description: My personal QC.
        ---
        USER-QC-BODY
    """)
    _w(p / "settings.yaml", """
        env:
          ABA_FROM_USER: "1"
        focus_tags:
          - user-pref-1
    """)
    return p


@pytest.fixture
def four_scope(tmp_path: Path):
    """The fixture: full 4-scope deployment, all bundles present."""
    return {
        "system": _build_system(tmp_path / "system"),
        "inst":   _build_institution(tmp_path / "inst"),
        "lab":    _build_lab(tmp_path / "lab"),
        "home":   tmp_path / "home",
    }


@pytest.fixture(autouse=True)
def _reset_cache():
    bundle_active._reset_for_testing()
    yield
    bundle_active._reset_for_testing()


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

def test_chain_includes_all_four_scopes(four_scope, monkeypatch):
    """Resolver discovers all four scopes; chain is broadest→narrowest."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    r = resolve_scopes()
    names = [s.name for s in r.scope_chain]
    assert names == ["system", "institution", "lab", "user"]
    assert all(s.present for s in r.scope_chain), \
        f"some scopes missing: {[(s.name, s.present) for s in r.scope_chain]}"


def test_policy_text_concatenation_order(four_scope, monkeypatch):
    """All four scope AGENTS.md markers appear in the rendered policy
    text in chain order."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    r = resolve_scopes()
    eb = load_bundle(r)
    policy = eb.policy_text
    for marker in ("SYS-IDENTITY-MARKER", "INST-POLICY-MARKER",
                    "LAB-POLICY-MARKER", "USER-PREFS-MARKER"):
        assert marker in policy, f"missing {marker} in composed policy"
    order = [policy.index(m) for m in (
        "SYS-IDENTITY-MARKER", "INST-POLICY-MARKER",
        "LAB-POLICY-MARKER", "USER-PREFS-MARKER")]
    assert order == sorted(order), \
        f"chain order violated; positions {order}"


def test_required_rules_are_additive(four_scope, monkeypatch):
    """Both system's `common.md` AND institution's same-named `common.md`
    appear in required_rules (additive across scopes)."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    eb = load_bundle(resolve_scopes())
    common_rules = [r for r in eb.required_rules if r.filename == "common.md"]
    assert len(common_rules) == 2, \
        f"expected 2 common.md rules, got {len(common_rules)}"
    sources = {r.source_scope for r in common_rules}
    assert sources == {"system", "institution"}
    # Both contents reachable.
    bodies = " ".join(r.content for r in common_rules)
    assert "SYS-COMMON-REQUIRED" in bodies
    assert "INST-COMMON-REQUIRED" in bodies


def test_overrideable_rule_shadowed_by_lab(four_scope, monkeypatch):
    """lab/rules/behavior.md wins over system/rules/behavior.md."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    eb = load_bundle(resolve_scopes())
    behavior = next(r for r in eb.overrideable_rules if r.filename == "behavior.md")
    assert behavior.source_scope == "lab"
    assert "LAB-BEHAVIOR-VERSION" in behavior.content
    assert "SYS-BEHAVIOR-VERSION" not in behavior.content
    # Provenance records system as shadowed.
    pr = eb.provenance.overrideable_files["behavior.md"]
    assert pr["effective_scope"] == "lab"
    assert "system" in pr["shadowed_in"]


def test_skill_override_user_wins(four_scope, monkeypatch):
    """User scope's qc.md beats system's qc.md (narrowest wins)."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    eb = load_bundle(resolve_scopes())
    qc = next(s for s in eb.skills if s.name == "qc")
    assert qc.source_scope == "user"
    assert "USER-QC-BODY" in qc.body
    # Provenance: system is recorded as shadowed.
    assert "system" in eb.provenance.skills["qc"]["shadowed_in"]


def test_disable_recipes_drops_skill(four_scope, monkeypatch):
    """`disable_recipes: [soon-disabled]` in institution settings.yaml
    drops that skill from the effective bundle."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    eb = load_bundle(resolve_scopes())
    names = {s.name for s in eb.skills}
    assert "soon-disabled" not in names, \
        "disable_recipes failed to drop the skill"
    # Provenance records it as disabled.
    assert eb.provenance.skills["soon-disabled"]["disabled"] is True


def test_agents_filter_drops_other_agent_skill(four_scope, monkeypatch):
    """A skill with `agents: ["other-agent"]` (no aba) is filtered out."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    eb = load_bundle(resolve_scopes())
    names = {s.name for s in eb.skills}
    assert "private-internal" not in names, \
        "agents filter did not drop the other-agent-only skill"
    assert eb.provenance.skills["private-internal"]["skipped_reason"]


def test_settings_dict_merge_scalar_narrowest_and_list_extend(four_scope, monkeypatch):
    """Scalar (default_model): lab overrides system. List (focus_tags): all
    scopes' values concatenate. Dict (env): keys from every scope present."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))

    eb = load_bundle(resolve_scopes())
    # default_model: lab is narrowest declarer.
    assert eb.settings["default_model"] == "claude-opus-lab"
    # advisor_model only declared in system → carries through.
    assert eb.settings["advisor_model"] == "claude-haiku-system"
    # env dict-merged across all 4 scopes.
    env = eb.settings["env"]
    assert env["ABA_FROM_SYSTEM"] == "1"
    assert env["ABA_FROM_INST"] == "1"
    assert env["ABA_FROM_LAB"] == "1"
    assert env["ABA_FROM_USER"] == "1"
    # focus_tags: list-extend (lab + user)
    tags = eb.settings["focus_tags"]
    assert "lab-preset-1" in tags
    assert "lab-preset-2" in tags
    assert "user-pref-1" in tags


def test_prompt_overlay_carries_non_system_policy(four_scope, monkeypatch):
    """The bundle_overlay block in build.py injects institution+lab+user
    policy into the rendered system prompt."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))
    bundle_active._reset_for_testing()

    from content.bio.prompts.build import build_system
    stable, _dynamic = build_system(
        active_tools=[], role="primary", intent="", ctx={})

    for marker in ("INST-POLICY-MARKER", "LAB-POLICY-MARKER",
                    "USER-PREFS-MARKER"):
        assert marker in stable, \
            f"{marker} did not reach the rendered prompt"

    # System policy reaches the prompt via identity.md (NOT via overlay,
    # which excludes the system scope to avoid duplication). In this
    # fixture, the fixture's system AGENTS.md is not loaded by identity.md
    # because identity.md is read from the live backend/content tree, so
    # SYS-IDENTITY-MARKER will NOT appear — that's expected. We only
    # validate non-system markers here.


def test_inspect_state_dict_against_fixture(four_scope, monkeypatch):
    """The /api/bundle/state shape on a full fixture exposes every
    layering signal admins need (4-scope chain, shadowed flag, disabled
    flag, agent_filtered flag, warnings)."""
    user_bundle = four_scope["home"] / ".aba" / "bundle"
    _build_user(user_bundle)

    monkeypatch.setenv("HOME", str(four_scope["home"]))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_SYSTEM_BUNDLE", str(four_scope["system"]))
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(four_scope["inst"]))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(four_scope["lab"]))
    bundle_active._reset_for_testing()

    eb = bundle_active.get_bundle()
    r = bundle_active.get_resolution()
    state = _state_dict(r, eb)

    names = [s["name"] for s in state["scope_chain"]]
    assert names == ["system", "institution", "lab", "user"]
    assert all(s["present"] for s in state["scope_chain"])
    # Composition summary signals.
    assert state["summary"]["overrideable_rules"]["shadowed"] >= 1
    assert state["summary"]["skills"]["disabled"] == 1
    assert state["summary"]["skills"]["agent_filtered"] == 1
    # Round-trips through json.
    json.dumps(state)
