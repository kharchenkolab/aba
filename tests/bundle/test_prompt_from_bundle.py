"""P1 Stage 1b — build.py sources system policy/rules from the bundle.

Guarantees:
  * byte-identity: for the system-only case each bundle-sourced rule equals the
    on-disk file (so the live prompt is unchanged);
  * a lab/institution override of a named rule (figures.md) now REACHES the
    prompt and shadows the system copy;
  * a new rule under a fresh filename reaches the prompt via the catch-all;
  * required/ rules and AGENTS.md overlays inject.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

TOOLS = [{"name": n, "description": "d"} for n in
         ("Skill", "present_plan", "read_memory", "run_python", "search_skills")]


def _reload():
    from core.bundle.active import reload_bundle
    reload_bundle()


def _prompt_full(intent: str = "make a umap") -> str:
    from content.bio.prompts.build import build_system
    st, dy = build_system(TOOLS, role="primary", intent=intent, mode="full",
                          ctx={"thread_id": "t"})
    return st + "\n" + dy


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Stand up a temp lab bundle (rules / required / AGENTS.md), resolve
    system+lab. Restores system-only after."""
    def _run(rules: dict | None = None, required: dict | None = None,
             agents: str = "Lab policy"):
        b = tmp_path / "lab"
        (b / "rules").mkdir(parents=True)
        (b / "AGENTS.md").write_text(agents)
        for fn, c in (rules or {}).items():
            (b / "rules" / fn).write_text(c)
        if required:
            (b / "rules" / "required").mkdir(exist_ok=True)
            for fn, c in required.items():
                (b / "rules" / "required" / fn).write_text(c)
        monkeypatch.setenv("ABA_LAB_BUNDLE", str(b))
        monkeypatch.setenv("ABA_GROUP", "kh")
        _reload()
        return b
    yield _run
    _reload()


def test_system_rule_equals_disk():
    """Byte-identity: bundle-sourced system rule == the on-disk file in
    system_bundle (the real home now — no content/bio symlinks)."""
    _reload()
    from content.bio.prompts.build import _bundle_rule_text
    SB = ROOT / "backend" / "system_bundle"

    def disk(f):
        for sub in ("rules", "rules/required"):
            p = SB / sub / f
            if p.is_file():
                return p.read_text().rstrip()
        return None
    for f in ("figures.md", "behavior.md", "data_orientation.md", "plan_first.md",
              "nonnegotiables.md", "recipes.md", "highlighting.md"):
        assert _bundle_rule_text(f) == disk(f), f


def test_lab_override_reaches_prompt(lab):
    lab({"figures.md": "# LAB FIGURE STYLE\nlab palette only"})
    p = _prompt_full()
    assert "LAB FIGURE STYLE" in p
    sys_fig = (ROOT / "backend" / "system_bundle" / "rules" / "figures.md").read_text()
    phrase = next(l.strip() for l in sys_fig.splitlines()
                  if len(l.strip()) > 40 and not l.startswith("#"))
    assert phrase not in p, "system figures.md should be shadowed by the lab override"


def test_new_lab_rule_via_catchall(lab):
    lab({"lab_safety.md": "# LAB SAFETY\nnever put PHI in a plot"})
    assert "LAB SAFETY" in _prompt_full()


def test_lab_required_rule_injected(lab):
    lab(required={"mandate.md": "# MANDATE\nalways cite the core facility"})
    assert "MANDATE" in _prompt_full()


def test_lab_policy_overlay_injected(lab):
    lab(agents="Lab standing policy ZZZ")
    assert "Lab standing policy ZZZ" in _prompt_full()


def test_system_only_prompt_has_system_rules():
    """Sanity: with no overlay, the named system rules are present."""
    _reload()
    p = _prompt_full()
    assert "umap" in p.lower() or "figure" in p.lower()   # figures.md present
