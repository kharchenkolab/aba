"""The promote gate for a single env store.

The gate it replaces compared two published trees and missed every failure
that mattered on 2026-08-27:

  * a pack in NEITHER tree was never iterated — `python-bio-cuda` was declared
    by site.yaml, derived on every stage, and never published anywhere, and
    the gate said "packs ok" (empty subject set satisfies a for-all claim);
  * it compared version STRINGS, so byte-copied squashfs images carrying the
    wrong internal prefix passed while being unbootable;
  * once the two trees became one it compared production against a leftover
    directory and refused a correct promote.

Run: python tests/test_promote_pin_gate.py   (or via pytest)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_pack_pins import DRIFT, FATAL, OK, check, parse_pins  # noqa: E402


def _store(tmp_path, envs: dict) -> str:
    (tmp_path / "catalog.json").write_text(json.dumps({"envs": envs}))
    return str(tmp_path)


def _pack(latest, versions):
    return {"latest": latest, "versions": {v: {} for v in versions}}


DECLARED = [("python-bio", "base:python"), ("r-bio", "base:r")]


def test_a_declared_pack_that_was_never_published_is_FATAL(tmp_path):
    """THE miss. The old gate iterated what EXISTED; a pack declared but never
    built was invisible to it and reported clean."""
    tree = _store(tmp_path, {"python-bio": _pack("2026.08.27-a", ["2026.08.27-a"])})
    rc, lines = check(tree, {}, DECLARED + [("python-bio-cuda", "site.yaml jobs.gpu_env_pack")])
    assert rc == FATAL
    assert any("PACK MISSING" in ln and "python-bio-cuda" in ln for ln in lines), lines
    assert any("r-bio" in ln and "PACK MISSING" in ln for ln in lines)


def test_a_pin_naming_a_version_that_is_not_in_the_store_is_FATAL(tmp_path):
    """Promoting onto a pin that does not resolve leaves the deployment
    adopting nothing — the half-applied state the gate exists to stop."""
    tree = _store(tmp_path, {"python-bio": _pack("2026.08.27-a", ["2026.08.27-a"]),
                             "r-bio": _pack("2026.08.27-b", ["2026.08.27-b"])})
    rc, lines = check(tree, {"r-bio": "2026.09.01-nope"}, DECLARED)
    assert rc == FATAL
    assert any("PIN UNPUBLISHED" in ln for ln in lines), lines


def test_a_pin_behind_latest_is_DRIFT_not_a_refusal(tmp_path):
    """Overridable on purpose: staging rides `latest`, so a production pin
    behind it ships something staging did not exercise — a judgement call an
    operator may legitimately make, not 'cannot work'."""
    tree = _store(tmp_path, {"python-bio": _pack("2026.08.27-a", ["2026.08.20-old", "2026.08.27-a"]),
                             "r-bio": _pack("2026.08.27-b", ["2026.08.27-b"])})
    rc, lines = check(tree, {"python-bio": "2026.08.20-old"}, DECLARED)
    assert rc == DRIFT
    assert any("DRIFT" in ln for ln in lines), lines


def test_pins_matching_latest_pass(tmp_path):
    """ARMED the other way: the correct configuration must pass, or the gate
    becomes a permanent red that gets overridden by reflex."""
    tree = _store(tmp_path, {"python-bio": _pack("2026.08.27-a", ["2026.08.27-a"]),
                             "r-bio": _pack("2026.08.27-b", ["2026.08.27-b"])})
    rc, _ = check(tree, {"python-bio": "2026.08.27-a", "r-bio": "2026.08.27-b"}, DECLARED)
    assert rc == OK
    rc2, _ = check(tree, {}, DECLARED)          # unpinned → latest
    assert rc2 == OK


def test_an_empty_declared_set_is_FATAL_not_clean(tmp_path):
    """WIDE — the degenerate input that produced the original bug. A bundle
    that failed to load declares nothing, and 'nothing to check' must never
    read as 'everything checks out'."""
    tree = _store(tmp_path, {"python-bio": _pack("v1", ["v1"])})
    rc, lines = check(tree, {}, [])
    assert rc == FATAL
    assert any("declares no env packs" in ln for ln in lines), lines


def test_an_unreadable_store_is_FATAL_not_clean(tmp_path):
    """The other degenerate input: if the store cannot be read, nothing was
    verified. Saying so is the whole job."""
    rc, lines = check(str(tmp_path / "does-not-exist"), {}, DECLARED)
    assert rc == FATAL
    assert any("cannot read the env store" in ln for ln in lines), lines


def test_pins_parse_from_the_inline_yaml_versions_env_carries():
    assert parse_pins("{}") == {}
    assert parse_pins("") == {}
    assert parse_pins("{python-bio: 2026.08.27-8d4389ba, r-bio: 2026.08.27-654521e9}") == {
        "python-bio": "2026.08.27-8d4389ba", "r-bio": "2026.08.27-654521e9"}


def _standalone() -> int:
    import tempfile, traceback
    rc = 0
    for t in (test_a_declared_pack_that_was_never_published_is_FATAL,
              test_a_pin_naming_a_version_that_is_not_in_the_store_is_FATAL,
              test_a_pin_behind_latest_is_DRIFT_not_a_refusal,
              test_pins_matching_latest_pass,
              test_an_empty_declared_set_is_FATAL_not_clean,
              test_an_unreadable_store_is_FATAL_not_clean,
              test_pins_parse_from_the_inline_yaml_versions_env_carries):
        try:
            n = t.__code__.co_argcount
            t(Path(tempfile.mkdtemp())) if n else t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(); print(f"  [FAIL] {t.__name__}: {e}"); rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
