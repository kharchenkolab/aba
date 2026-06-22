"""Tests for the bundle.settings → runtime wiring.

Spec resolution chain (per core.runtime.agent.resolve_primary_spec_name):
    1. ABA_PRIMARY_SPEC env
    2. EffectiveBundle.settings["primary_spec"]
    3. "guide" fallback

Model resolution chain (per core.config.current_model_for_primary):
    1. ABA_PRIMARY_MODEL / ABA_MODEL env
    2. ~/.aba/config.env ABA_MODEL=
    3. EffectiveBundle.settings["default_model"]
    4. default arg (spec YAML)
    5. MODEL module constant

We pin each layer in turn and confirm precedence + fall-through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle import active as bundle_active     # noqa: E402
from core.bundle.loader import EffectiveBundle      # noqa: E402


@pytest.fixture
def fake_bundle(monkeypatch):
    """Replace get_bundle()'s cache with a custom EffectiveBundle whose
    .settings dict we control per test. Restored automatically."""
    bundle_active._reset_for_testing()

    def _install(settings: dict) -> EffectiveBundle:
        eb = EffectiveBundle()
        eb.settings = dict(settings or {})
        # Bypass the lazy-init path: stash the EB directly so get_bundle()
        # returns ours without touching the filesystem.
        bundle_active._cached_bundle = eb
        # Resolution doesn't need to be populated for these tests; the
        # functions under test only read .settings.
        return eb

    yield _install
    bundle_active._reset_for_testing()


# -------------------------------------------------------------------
# resolve_primary_spec_name
# -------------------------------------------------------------------

def test_spec_env_wins_over_bundle(monkeypatch, fake_bundle):
    fake_bundle({"primary_spec": "lean_guide"})
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "grounded_guide")
    from core.runtime.agent import resolve_primary_spec_name
    assert resolve_primary_spec_name() == "grounded_guide"


def test_spec_bundle_when_env_absent(monkeypatch, fake_bundle):
    fake_bundle({"primary_spec": "lean_guide"})
    monkeypatch.delenv("ABA_PRIMARY_SPEC", raising=False)
    from core.runtime.agent import resolve_primary_spec_name
    assert resolve_primary_spec_name() == "lean_guide"


def test_spec_fallback_when_bundle_key_missing(monkeypatch, fake_bundle):
    fake_bundle({})                     # no primary_spec key
    monkeypatch.delenv("ABA_PRIMARY_SPEC", raising=False)
    from core.runtime.agent import resolve_primary_spec_name
    assert resolve_primary_spec_name() == "guide"


def test_spec_bundle_value_stripped(monkeypatch, fake_bundle):
    """Whitespace around the bundle's primary_spec is trimmed."""
    fake_bundle({"primary_spec": "  grounded_guide  "})
    monkeypatch.delenv("ABA_PRIMARY_SPEC", raising=False)
    from core.runtime.agent import resolve_primary_spec_name
    assert resolve_primary_spec_name() == "grounded_guide"


def test_spec_bundle_non_string_ignored(monkeypatch, fake_bundle):
    """A non-string primary_spec value falls through to the default."""
    fake_bundle({"primary_spec": 42})
    monkeypatch.delenv("ABA_PRIMARY_SPEC", raising=False)
    from core.runtime.agent import resolve_primary_spec_name
    assert resolve_primary_spec_name() == "guide"


def test_spec_env_empty_string_treated_as_unset(monkeypatch, fake_bundle):
    """An empty ABA_PRIMARY_SPEC falls through to the bundle."""
    fake_bundle({"primary_spec": "lean_guide"})
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "  ")
    from core.runtime.agent import resolve_primary_spec_name
    assert resolve_primary_spec_name() == "lean_guide"


# -------------------------------------------------------------------
# current_model_for_primary
# -------------------------------------------------------------------

def test_model_env_wins_over_bundle(monkeypatch, fake_bundle, tmp_path):
    fake_bundle({"default_model": "claude-bundle-model"})
    monkeypatch.setenv("ABA_MODEL", "claude-env-model")
    monkeypatch.setenv("ABA_HOME", str(tmp_path))      # empty config.env dir
    monkeypatch.delenv("ABA_PRIMARY_MODEL", raising=False)
    from core.config import current_model_for_primary
    assert current_model_for_primary(default="SPEC_YAML") == "claude-env-model"


def test_model_primary_env_wins(monkeypatch, fake_bundle, tmp_path):
    fake_bundle({"default_model": "claude-bundle-model"})
    monkeypatch.setenv("ABA_PRIMARY_MODEL", "claude-primary-env")
    monkeypatch.setenv("ABA_MODEL", "claude-fallback-env")
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    from core.config import current_model_for_primary
    assert current_model_for_primary() == "claude-primary-env"


def test_model_bundle_when_env_absent(monkeypatch, fake_bundle, tmp_path):
    fake_bundle({"default_model": "claude-bundle-model"})
    monkeypatch.delenv("ABA_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("ABA_MODEL", raising=False)
    monkeypatch.setenv("ABA_HOME", str(tmp_path))      # no config.env
    from core.config import current_model_for_primary
    assert current_model_for_primary(default="SPEC_YAML") == "claude-bundle-model"


def test_model_falls_through_to_spec_default(monkeypatch, fake_bundle, tmp_path):
    fake_bundle({})                                     # no default_model
    monkeypatch.delenv("ABA_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("ABA_MODEL", raising=False)
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    from core.config import current_model_for_primary
    assert current_model_for_primary(default="SPEC_YAML") == "SPEC_YAML"


def test_model_config_env_wins_over_bundle(monkeypatch, fake_bundle, tmp_path):
    """~/.aba/config.env ABA_MODEL still beats the bundle layer."""
    fake_bundle({"default_model": "claude-bundle-model"})
    monkeypatch.delenv("ABA_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("ABA_MODEL", raising=False)
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    (tmp_path / "config.env").write_text("export ABA_MODEL=claude-cfgenv\n")
    from core.config import current_model_for_primary
    assert current_model_for_primary(default="SPEC_YAML") == "claude-cfgenv"


def test_model_bundle_failure_falls_through(monkeypatch, tmp_path):
    """If get_bundle() raises, the chain still produces a value (the
    spec-supplied default)."""
    bundle_active._reset_for_testing()

    def _boom():
        raise RuntimeError("simulated bundle failure")
    monkeypatch.setattr(bundle_active, "get_bundle", _boom)

    monkeypatch.delenv("ABA_PRIMARY_MODEL", raising=False)
    monkeypatch.delenv("ABA_MODEL", raising=False)
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    from core.config import current_model_for_primary
    assert current_model_for_primary(default="SPEC_YAML") == "SPEC_YAML"


# -------------------------------------------------------------------
# System bundle declares grounded_guide (the deployment-shipping default)
# -------------------------------------------------------------------

def test_system_bundle_settings_yaml_declares_primary_spec():
    """Regression guard: the file we ship sets primary_spec — bumping
    this is intentional but should be conscious. Update the assertion
    when you intentionally change the system default."""
    import yaml
    p = ROOT / "backend" / "system_bundle" / "settings.yaml"
    data = yaml.safe_load(p.read_text())
    assert data.get("primary_spec") == "grounded_guide"
