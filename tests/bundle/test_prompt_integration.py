"""P3 integration tests: EffectiveBundle reaches the rendered prompt.

These tests cover the wiring in backend/content/bio/prompts/build.py:
the `bundle_overlay` _Block calls `get_bundle().policy_text_excluding(
{"system"})` so institution/lab/user policy text appears in the rendered
system prompt while the system scope's content keeps coming through the
existing `identity` block (avoiding duplication).

Goal: verify
  (a) Mac default (system-only chain) produces the same output as before
      the overlay block existed — i.e. the block contributes "" and the
      assembler drops it.
  (b) A non-system scope's AGENTS.md content actually reaches the
      rendered prompt when present.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle import active as bundle_active            # noqa: E402
from core.bundle.loader import EffectiveBundle             # noqa: E402
from core.bundle.scope_resolver import (                   # noqa: E402
    ScopeBundle, ScopeResolution,
)


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _mk_bundle(p: Path, agents_md: str) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    (p / "AGENTS.md").write_text(agents_md)
    return p


@pytest.fixture(autouse=True)
def _reset_cache():
    """Drop the module-level bundle cache between tests."""
    bundle_active._reset_for_testing()
    yield
    bundle_active._reset_for_testing()


# -------------------------------------------------------------------
# Tests
# -------------------------------------------------------------------

def test_mac_default_overlay_is_empty():
    """No env vars, no site.yaml → bundle_overlay contributes empty
    string. The assembler drops empty blocks so the prompt is byte-
    identical with pre-bundle behavior."""
    from content.bio.prompts.build import _bundle_overlay
    out = _bundle_overlay([])
    assert out == "", \
        f"expected empty overlay on system-only chain, got {len(out)} chars"


def test_lab_bundle_content_reaches_prompt(tmp_path: Path, monkeypatch):
    """When a lab bundle is present with non-trivial AGENTS.md, its
    content shows up in the rendered system prompt."""
    home = tmp_path / "home"
    home.mkdir()
    lab = _mk_bundle(
        tmp_path / "groups" / "kharchenko" / "aba" / "bundle",
        "LAB-POLICY-MARKER-7QXJ: only use spliced counts.\n",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(lab))

    # Force a re-resolution with the patched env.
    bundle_active._reset_for_testing()
    eb = bundle_active.get_bundle()
    overlay = eb.policy_text_excluding({"system"})
    assert "LAB-POLICY-MARKER-7QXJ" in overlay, \
        f"lab AGENTS.md content missing from overlay; got: {overlay[:200]!r}"

    # And it must reach the rendered system prompt.
    from content.bio.prompts.build import build_system
    stable, _dynamic = build_system(
        active_tools=[], role="primary", intent="", ctx={})
    assert "LAB-POLICY-MARKER-7QXJ" in stable, \
        "lab policy text didn't reach the rendered system prompt"


def test_institution_and_lab_both_appear(tmp_path: Path, monkeypatch):
    """Both scopes contribute when both bundles are present, in chain
    order (broadest first → institution before lab)."""
    home = tmp_path / "home"
    home.mkdir()
    inst = _mk_bundle(
        tmp_path / "cluster" / "aba" / "institution",
        "INST-POLICY-A1B2: cite VBC core facilities.\n",
    )
    lab = _mk_bundle(
        tmp_path / "groups" / "kharchenko" / "aba" / "bundle",
        "LAB-POLICY-C3D4: prefer pagoda2 for clustering.\n",
    )

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USER", "alice")
    monkeypatch.setenv("ABA_GROUP", "kharchenko")
    monkeypatch.setenv("ABA_INSTITUTION_BUNDLE", str(inst))
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(lab))

    bundle_active._reset_for_testing()
    eb = bundle_active.get_bundle()
    overlay = eb.policy_text_excluding({"system"})

    assert "INST-POLICY-A1B2" in overlay
    assert "LAB-POLICY-C3D4" in overlay
    # Order: institution (broader) before lab (narrower).
    assert overlay.index("INST-POLICY-A1B2") < overlay.index("LAB-POLICY-C3D4"), \
        "expected institution to appear before lab in chain order"


def test_overlay_failure_does_not_break_prompt(monkeypatch):
    """If bundle resolution throws, _bundle_overlay returns '' and the
    prompt still assembles. Defensive against bundle-side regressions."""
    from content.bio.prompts import build as build_mod

    def _boom():
        raise RuntimeError("simulated bundle failure")

    # Monkey-patch the import target so _bundle_overlay's lazy import
    # gets our broken version.
    import core.bundle.active as active_mod
    monkeypatch.setattr(active_mod, "get_bundle", _boom)

    out = build_mod._bundle_overlay([])
    assert out == "", \
        "expected empty overlay on bundle failure, not an exception bubble"

    stable, _ = build_mod.build_system(
        active_tools=[], role="primary", intent="", ctx={})
    assert stable, "prompt should still assemble even with bundle errors"
