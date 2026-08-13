"""The substrate identity instrument (regtest/harness/substrate.py).

A regtest verdict is a statement about a PAIRING — the aba tree under test and
the weft substrate it calls — and the harness recorded only the first half.
When two weft copies are importable, which one answers is decided by
interpreter flags (PYTHONNOUSERSITE / site.ENABLE_USER_SITE) rather than by the
deployment, so the same tree measured twice yields two verdicts and neither
names its substrate. On 2026-08-13 exactly that produced three false failures
and one wrong diagnosis handed to another engineer.

What is pinned here is the DISCRIMINATION, not just the refusal: the module has
to refuse the ambiguous shape while staying quiet on the shapes that are merely
untidy. A blanket "more than one copy" rule would fire on every large env
(namespace packages, vendored trees) and would be turned off within a week.

  * two DIFFERENT copies                 -> refuse (unattributable)
  * byte-identical copies in two places  -> allow (untidy, not a hazard)
  * duplicates OUTSIDE the substrate set -> allow (not what a verdict is about)
  * one copy / no copy                   -> allow, and stamp says which

The last two are the degenerate shapes: `absent` is what a bare test process
looks like, and the out-of-set case is what every real venv looks like.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "aba_substrate", ROOT / "regtest" / "harness" / "substrate.py")
substrate = importlib.util.module_from_spec(_spec)
sys.modules["aba_substrate"] = substrate
_spec.loader.exec_module(substrate)

pytestmark = pytest.mark.platform

_BODY = "VERSION = {!r}\n"


def _copy(root: Path, where: str, name: str, body: str) -> str:
    """Write an importable package `name` under `root/where`; return that dir.

    `where` is explicit rather than derived from the content: deriving it meant
    two byte-identical copies landed in ONE directory, `copies()` de-duplicated
    it by realpath, and the two-copy tests silently measured a single copy."""
    d = root / where
    pkg = d / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body)
    return str(d)


def _assert_two_distinct_dirs(a: str, b: str) -> None:
    """The premise of every two-copy case below. Without it a de-duplicated
    path list turns these tests into assertions about one copy."""
    assert a != b, "test set-up collapsed both copies into one directory"
    assert len(substrate.copies("weft", [a, b])) == 2, \
        "both copies must be importable, or the case under test never arises"


def test_two_different_copies_are_refused(tmp_path):
    """The measurement hazard: first-on-path wins, and that is an interpreter
    flag, not a deployment fact."""
    a = _copy(tmp_path, "siteA", "weft", _BODY.format("A"))
    b = _copy(tmp_path, "siteB", "weft", _BODY.format("B"))
    _assert_two_distinct_dirs(a, b)
    problems = substrate.check_substrate(("weft",), [a, b])
    assert problems, "two distinct weft copies must be refused"
    joined = "\n".join(problems)
    # The message has to name BOTH paths, or the reader cannot act on it.
    assert a in joined and b in joined
    assert "unattributable" in joined.lower()


def test_identical_copies_in_two_places_are_allowed(tmp_path):
    """Untidy, not a hazard: whichever wins, the bytes that answer are the
    same. Refusing here would make the gate fire on ordinary venv layouts."""
    body = _BODY.format("SAME")
    a = _copy(tmp_path, "siteA", "weft", body)
    b = _copy(tmp_path, "siteB", "weft", body)
    _assert_two_distinct_dirs(a, b)
    assert substrate.check_substrate(("weft",), [a, b]) == []
    # ...and the duplication is still VISIBLE in the stamp, never hidden.
    assert "shadowed" in substrate.stamp(("weft",), [a, b])


def test_duplicate_outside_the_substrate_set_is_ignored(tmp_path):
    """Only packages whose identity decides what a red result MEANS are
    refusal-worthy; a blanket rule trips on every vendored tree."""
    a = _copy(tmp_path, "siteA", "notweft", _BODY.format("A"))
    b = _copy(tmp_path, "siteB", "notweft", _BODY.format("B"))
    assert len({c["hash"] for c in substrate.copies("notweft", [a, b])}) == 2, \
        "premise: the out-of-set package really does differ between the two"
    assert substrate.check_substrate(("weft",), [a, b]) == []


def test_single_copy_is_clean_and_named(tmp_path):
    a = _copy(tmp_path, "siteA", "weft", _BODY.format("ONLY"))
    assert substrate.check_substrate(("weft",), [a]) == []
    stamp = substrate.stamp(("weft",), [a])
    assert a in stamp and "shadowed" not in stamp


def test_absent_substrate_does_not_crash_or_refuse(tmp_path):
    """A bare process importing nothing must not be refused — it has no
    verdict to attribute. It must still SAY the substrate was absent."""
    empty = str(tmp_path / "empty")
    Path(empty).mkdir()
    assert substrate.check_substrate(("weft",), [empty]) == []
    assert "weft=absent" in substrate.stamp(("weft",), [empty])


def test_hash_ignores_pyc_and_metadata(tmp_path):
    """Two installs of the same source differ in .pyc mtimes and RECORD; if
    those counted, every deployment would read as a distinct substrate and the
    gate would refuse constantly."""
    body = _BODY.format("SAME")
    a = _copy(tmp_path, "siteA", "weft", body)
    b = _copy(tmp_path, "siteB", "weft", body)
    _assert_two_distinct_dirs(a, b)
    (Path(b) / "weft" / "__pycache__").mkdir()
    (Path(b) / "weft" / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"\x00\x01")
    (Path(b) / "weft-0.1.0.dist-info").mkdir()
    (Path(b) / "weft-0.1.0.dist-info" / "RECORD").write_text("weft/__init__.py,,\n")
    assert substrate.check_substrate(("weft",), [a, b]) == []


def test_sweep_gate_delegates_to_this_module():
    """The sweep's pre-flight must keep calling THIS check. Re-implementing it
    there is how the two drift and the gate quietly stops matching the stamp."""
    _s = importlib.util.spec_from_file_location(
        "aba_sweep_gate", ROOT / "regtest" / "harness" / "sweep.py")
    sweep = importlib.util.module_from_spec(_s)
    sys.modules["aba_sweep_gate"] = sweep
    _s.loader.exec_module(sweep)
    assert sweep._substrate_mod().check_substrate is substrate.check_substrate \
        or sweep._substrate_mod().__file__ == substrate.__file__


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
