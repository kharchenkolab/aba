"""Tests for the `aba-bundle inspect` CLI.

Covers:
  - Pretty-print mode contains the user/scope/state-dir cues.
  - --json mode emits a valid, parseable state dict.
  - --reload forces re-resolution (the cache is dropped + rebuilt).
  - state_dict() shape matches what the /api/bundle/state route returns.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle import active as bundle_active   # noqa: E402
from core.bundle.cli import main, _state_dict     # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    bundle_active._reset_for_testing()
    yield
    bundle_active._reset_for_testing()


def _run_cli(*args: str) -> str:
    """Invoke main(argv=...) and capture stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(list(args))
    assert rc == 0, f"CLI returned non-zero: {rc}"
    return buf.getvalue()


def test_inspect_pretty_default():
    """No args → defaults to inspect, pretty output. Mentions user,
    scope, and state_dir."""
    out = _run_cli("inspect")
    assert "[scope]" in out
    assert "state_dir" in out
    assert "[bundle]" in out


def test_inspect_no_subcommand_falls_back():
    """Running with no subcommand is equivalent to `inspect`."""
    out = _run_cli()
    assert "[scope]" in out


def test_inspect_json_is_parseable():
    """--json output round-trips through json.loads and has the
    documented top-level keys."""
    out = _run_cli("inspect", "--json")
    data = json.loads(out)
    for key in ("user", "scope_chain", "state_dir", "summary",
                "warnings", "errors"):
        assert key in data, f"missing key {key}"
    # scope_chain entries shape
    for s in data["scope_chain"]:
        assert {"name", "label", "path", "present", "optional"} <= s.keys()
    # summary substructure
    summary = data["summary"]
    assert "skills" in summary and "total" in summary["skills"]


def test_inspect_reload_drops_cache(monkeypatch, tmp_path):
    """--reload re-resolves. We verify by changing env between calls and
    confirming the second call sees the new state."""
    # First call: no lab bundle.
    bundle_active._reset_for_testing()
    out_a = _run_cli("inspect", "--json")
    data_a = json.loads(out_a)
    # Sanity: a 'lab' scope is not in the first chain (no env var set).
    assert not any(s["name"] == "lab" for s in data_a["scope_chain"])

    # Set the lab env var, then --reload.
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "AGENTS.md").write_text("# lab\n")
    monkeypatch.setenv("ABA_LAB_BUNDLE", str(lab))
    monkeypatch.setenv("ABA_GROUP", "kharchenko")

    out_b = _run_cli("inspect", "--reload", "--json")
    data_b = json.loads(out_b)
    lab_scope = next((s for s in data_b["scope_chain"]
                        if s["name"] == "lab"), None)
    assert lab_scope is not None, \
        "lab scope should appear in chain after --reload"
    assert lab_scope["present"] is True


def test_state_dict_matches_api_shape():
    """The CLI's _state_dict and the /api/bundle/state route share the
    same builder, so this test pins the public JSON contract."""
    eb = bundle_active.get_bundle()
    r = bundle_active.get_resolution()
    d = _state_dict(r, eb)

    # Stable top-level shape (contract for UI consumers).
    expected_keys = {"user", "group", "scope_chain", "state_dir",
                     "scratch_dir", "site_config", "composed_bundle",
                     "summary", "warnings", "errors"}
    assert expected_keys <= d.keys(), \
        f"missing keys: {expected_keys - d.keys()}"

    expected_summary_keys = {"policy_scopes", "required_rules",
                              "overrideable_rules", "skills",
                              "settings_top_level_keys"}
    assert expected_summary_keys <= d["summary"].keys()
