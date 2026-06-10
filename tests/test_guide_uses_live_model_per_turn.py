"""guide.py resolves the primary model at the turn boundary via
config.current_model_for_primary() — *not* the captured AgentSpec.model.

Without this test, a regression that reverts to `spec.model` would silently
re-bake the model at startup and break the tray's hot-switch promise.
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDE_PY = ROOT / "backend" / "guide.py"


def test_guide_model_is_resolved_via_current_model_for_primary_each_turn():
    """guide.py must call current_model_for_primary(default=spec.model …)
    at the start of every turn. A bare `guide_model = spec.model` would
    capture the startup AgentSpec value once and never re-read; that's the
    regression to guard against."""
    src = GUIDE_PY.read_text()
    # The function must be referenced
    assert "current_model_for_primary" in src, (
        "guide.py doesn't reference current_model_for_primary — primary "
        "model resolution is back to the captured AgentSpec value, which "
        "the tray / Control-page model selector can't hot-switch.")
    # Specifically: NOT a bare spec.model assignment for guide_model.
    bare = re.search(r"guide_model\s*=\s*spec\.model\b", src)
    assert not bare, (
        "Bare `guide_model = spec.model` found; this captures the YAML "
        "model at startup and breaks hot-switching. Use "
        "current_model_for_primary(default=spec.model if spec else None) "
        "instead.")


def test_guide_imports_current_model_for_primary_from_core_config():
    """The import surface name has to come from core.config so the contract
    with that module is explicit and a typo there fails noisily."""
    src = GUIDE_PY.read_text()
    # Either an explicit `from core.config import current_model_for_primary`
    # or a `from core import config` followed by `config.current_model_for_primary`.
    assert (
        "from core.config import" in src and
        "current_model_for_primary" in src
    ) or (
        "config.current_model_for_primary" in src
    ), (
        "guide.py imports current_model_for_primary in some shape from "
        "core.config — couldn't find either form")
