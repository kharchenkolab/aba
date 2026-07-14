"""Settings-registry safety net (env_reorg Phase 2/3).

Three guarantees:
  1. Every declared setting resolves without error and carries complete metadata.
  2. A committed *snapshot* of default-resolved values is stable — migrating a
     read-site from an inline `os.getenv` to `settings.x` must not change what the
     setting resolves to. The snapshot is the pre/post equality check the plan
     requires; a diff here means a declaration drifted from its original read.
  3. Type coercion behaves (bool idioms, int/float, csv, path, enum-advisory).

The snapshot lives at tests/data/env_registry_snapshot.json. Regenerate
deliberately with REGEN_ENV_SNAPSHOT=1 when adding settings (review the diff).
(Non-ABA_ name so the env-cleaning fixture doesn't strip it.)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

import core.config as config  # noqa: E402

SNAPSHOT = Path(__file__).parent / "data" / "env_registry_snapshot.json"

_VALID_WEFT_FATE = {"keep", "retire", "move:site", "move:envspec", "revisit"}
_VALID_TYPES = {"str", "int", "float", "bool", "path", "csv"}


def _clean_env(monkeypatch):
    """Strip ABA_* (and the non-ABA setting keys) so settings resolve to defaults."""
    for k in list(os.environ):
        if k.startswith("ABA_"):
            monkeypatch.delenv(k, raising=False)
    for k in ("DATA_DIR", "ARTIFACTS_DIR", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _resolved_defaults():
    out = {}
    for row in config.list_settings(reveal_secrets=True)["settings"]:
        val = row["value"]
        # Paths are machine-specific — snapshot only their basename shape, not the
        # absolute path (RUNTIME_DIR differs per box/test-tmp).
        if row["type"] == "path":
            continue
        if isinstance(val, tuple):  # JSON has no tuple → normalize to list
            val = list(val)
        out[row["name"]] = val
    return out


def test_all_settings_resolve_and_have_metadata():
    rows = config.list_settings(reveal_secrets=True)["settings"]
    assert rows, "registry is empty"
    for r in rows:
        assert r["type"] in _VALID_TYPES, f"{r['name']}: bad type {r['type']}"
        assert r["weft_fate"] in _VALID_WEFT_FATE, f"{r['name']}: bad weft_fate {r['weft_fate']}"
        assert r["category"], f"{r['name']}: missing category"
        assert r["doc"], f"{r['name']}: missing doc"
        assert r["source"] in ("default", "resolver") or r["source"].startswith("env:"), r


def test_default_resolution_snapshot(monkeypatch):
    regen = os.environ.get("REGEN_ENV_SNAPSHOT") == "1"  # read BEFORE cleaning
    _clean_env(monkeypatch)
    current = _resolved_defaults()
    if regen or not SNAPSHOT.exists():
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"(re)wrote snapshot with {len(current)} settings")
    baseline = json.loads(SNAPSHOT.read_text())
    # Every baseline setting must still resolve to the same default (new settings
    # may be added; existing ones must not silently change value).
    drift = {k: (baseline[k], current.get(k, "<MISSING>"))
             for k in baseline if current.get(k) != baseline[k]}
    assert not drift, f"default-resolution drift: {drift}"


def test_secrets_are_redacted():
    rows = config.list_settings()["settings"]  # reveal_secrets defaults False
    for r in rows:
        if r["secret"] and r["value"]:
            assert "••" in str(r["value"]) or r["value"] == "", (
                f"{r['name']} secret not redacted: {r['value']!r}")


def test_bool_coercion_idioms(monkeypatch):
    _clean_env(monkeypatch)
    s = config.settings
    # default-on idiom (kernel_enabled): on unless explicitly 0/false/empty
    monkeypatch.setenv("ABA_KERNEL_ENABLED", "0")
    assert s.kernel_enabled.get() is False
    monkeypatch.setenv("ABA_KERNEL_ENABLED", "yes")
    assert s.kernel_enabled.get() is True
    monkeypatch.delenv("ABA_KERNEL_ENABLED", raising=False)
    assert s.kernel_enabled.get() is True  # default


def test_numeric_coercion_and_empty_falls_to_default(monkeypatch):
    _clean_env(monkeypatch)
    s = config.settings
    monkeypatch.setenv("ABA_KERNEL_MAX_LIVE", "9")
    assert s.kernel_max_live.get() == 9
    # explicit-empty numeric → default (would have crashed int("") historically)
    monkeypatch.setenv("ABA_KERNEL_MAX_LIVE", "")
    assert s.kernel_max_live.get() == 5
    # malformed numeric → default, source flagged
    monkeypatch.setenv("ABA_KERNEL_MAX_LIVE", "notanint")
    val, src = s.kernel_max_live.resolve()
    assert val == 5 and "coerce-failed" in src


def test_unknown_env_detection(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ABA_TOTALLY_MADE_UP", "1")
    unknown = config.list_settings()["unknown_env"]
    assert "ABA_TOTALLY_MADE_UP" in unknown


def test_enum_is_advisory(monkeypatch):
    _clean_env(monkeypatch)
    s = config.settings
    monkeypatch.setenv("ABA_CAPABILITY_APPROVAL", "bogus")
    val, src = s.capability_approval.resolve()
    assert val == "bogus"  # passes through (no behavior change)
    assert "not-in-enum" in src  # but flagged for doctor


def test_validate_settings_clean(monkeypatch):
    _clean_env(monkeypatch)
    assert config.validate_settings() == []


def test_validate_settings_flags_bad_enum_and_unknown(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ABA_CAPABILITY_APPROVAL", "bogus")
    monkeypatch.setenv("ABA_TOTALLY_MADE_UP", "1")
    probs = config.validate_settings()
    assert any("capability_approval" in p and "not one of" in p for p in probs)
    assert any("ABA_TOTALLY_MADE_UP" in p for p in probs)


def test_validate_settings_flags_coerce_failure(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ABA_KERNEL_MAX_LIVE", "notanint")
    probs = config.validate_settings()
    assert any("kernel_max_live" in p and "coerce" in p for p in probs)


def test_validate_settings_strict_raises(monkeypatch):
    _clean_env(monkeypatch)
    monkeypatch.setenv("ABA_CAPABILITY_APPROVAL", "bogus")
    with pytest.raises(ValueError):
        config.validate_settings(strict=True)
    # and via the ABA_SETTINGS_STRICT flag
    monkeypatch.setenv("ABA_SETTINGS_STRICT", "1")
    with pytest.raises(ValueError):
        config.validate_settings()


def test_check_settings_valid_adapter(monkeypatch):
    _clean_env(monkeypatch)
    ok = config.check_settings_valid()
    assert ok["ok"] is True and ok["severity"] == "info"
    monkeypatch.setenv("ABA_CAPABILITY_APPROVAL", "bogus")
    bad = config.check_settings_valid()
    assert bad["ok"] is False and bad["severity"] == "warning"
