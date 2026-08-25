"""The pinned cross-repo envelope contract (ensure_available v1) — aba's mirror.

The substrate documents the result envelope in its schema file and guards it
with its own conformance test; aba vendors a copy (tests/schemas/) and guards
it here from the CONSUMER side, per the converged design: envelope changes are
deliberate, versioned events that fail loudly in both repos, never silent
field drift discovered in production.

Two instruments:
  1. DRIFT — the vendored copy must byte-match the sibling weft checkout's
     schema when one is present (dev boxes; CI without a sibling skips). A
     mismatch means the contract moved: update the vendored copy, the
     vocabulary pins, and the fixtures together, deliberately.
  2. EXECUTABLE READING — `check_envelope()` is aba's understanding of the
     documented shape, applied to canonical fixtures. This is the function the
     render stage adopts when execute() moves onto the verb (env_refi2 F-V2):
     the contract is checked exactly where payloads cross into aba's model.
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDORED = ROOT / "tests" / "schemas" / "ensure_envelope.schema.json"

# WHERE the substrate checkout might be. One hard-coded guess
# (~/aba/weft/...) meant this instrument silently skipped on every box that
# keeps weft anywhere else — including the one it was written on, where the
# checkout lives under /scratch. A cross-repo drift guard that never runs is
# the same defect as no guard, minus the honesty: found 2026-08-25 with 72
# unexamined substrate commits waiting, any of which could have moved the
# contract. Look in the places weft actually lives, and let a box say so
# explicitly with ABA_WEFT_SRC.
_REL = ("documentation", "ensure_envelope.schema.json")


def _sibling_candidates() -> "list[Path]":
    import os
    out: list[Path] = []
    env = (os.environ.get("ABA_WEFT_SRC") or "").strip()
    if env:
        out.append(Path(env).expanduser())
    home = Path.home()
    out += [home / "aba" / "weft",
            home / "weft",
            home / "weft-src" / "weft",
            ROOT.parent / "weft",
            Path("/scratch/users") / home.name / "aba-weft" / "repo" / "weft"]
    return [c.joinpath(*_REL) for c in out]


def _sibling() -> "Path | None":
    return next((p for p in _sibling_candidates() if p.exists()), None)


SIBLING = _sibling()

pytestmark = pytest.mark.platform

SPEC = json.loads(VENDORED.read_text())

# vocabulary pins, parsed from the vendored spec so they cannot drift from it
LANES = set(SPEC["attempt"]["lane"].split("|"))
OUTCOMES = set(SPEC["attempt"]["outcome"].split("|"))
SKIP_REASONS = set(SPEC["attempt"]["skip_reason"].split(" ")[0].split("|"))
VERIFY_STATUSES = {"passed", "failed", "unknown"}


def check_envelope(env: dict) -> "list[str]":
    """aba's executable reading of the v1 contract → violations."""
    out: list[str] = []
    if "error" in env:                      # standard error envelope + hints
        for k in ("stage", "detail", "hints"):
            if k not in env:
                out.append(f"error envelope missing {k!r}")
        hints = env.get("hints") or {}
        attempts = hints.get("attempts", [])
    else:
        for k in ("satisfied", "changed", "attempts", "verified", "runtime"):
            if k not in env:
                out.append(f"success envelope missing {k!r}")
        if env.get("satisfied") is not True:
            out.append("success envelope with satisfied != true")
        if env.get("changed") is False and env.get("attempts"):
            out.append("changed=false (pre-check) but attempts is non-empty")
        attempts = env.get("attempts", [])
        for name, v in (env.get("verified") or {}).items():
            if v.get("status") not in VERIFY_STATUSES:
                out.append(f"verified[{name}].status {v.get('status')!r} "
                           f"not in {sorted(VERIFY_STATUSES)}")
    for i, a in enumerate(attempts):
        if a.get("lane") not in LANES:
            out.append(f"attempt[{i}].lane {a.get('lane')!r} unknown")
        oc = a.get("outcome")
        if oc not in OUTCOMES:
            out.append(f"attempt[{i}].outcome {oc!r} unknown")
        if oc in ("failed", "refused") and not isinstance(a.get("error"), dict):
            out.append(f"attempt[{i}] {oc} without the verbatim typed error")
        if oc == "skipped":
            if a.get("skip_reason") not in SKIP_REASONS:
                out.append(f"attempt[{i}].skip_reason {a.get('skip_reason')!r}")
            if "seconds" in a:
                out.append(f"attempt[{i}] skipped must not carry seconds")
    return out


def test_vendored_matches_sibling_checkout():
    if SIBLING is None:
        # Name every path tried. A bare "no checkout here" reads as "nothing to
        # do"; the list makes a WRONG guess visible instead of invisible.
        pytest.skip("no weft checkout found — looked in: "
                    + ", ".join(str(p) for p in _sibling_candidates())
                    + " (set ABA_WEFT_SRC to the checkout root)")
    assert VENDORED.read_text() == SIBLING.read_text(), (
        "envelope contract drift: the substrate's schema differs from the "
        "vendored copy — a versioned contract event; update tests/schemas/, "
        "the vocabulary pins, and the fixtures together, deliberately")


def test_vendored_is_v1_with_the_converged_vocabulary():
    assert SPEC["envelope_version"] == 1
    assert {"installed", "installed_unverified", "failed",
            "refused", "skipped"} <= OUTCOMES
    assert {"conda", "pypi", "cran", "installer"} <= LANES
    assert {"halted", "budget", "grammar"} <= SKIP_REASONS


def test_additive_attempt_keys_stay_declared():
    """The per-attempt provenance keys aba READS must remain in the contract.
    `shadows_base` is the overlay's base-shadow warning — the earliest signal
    of a base-contradicting leaf (it was being dropped by the envelope
    entirely, which is how one pypi add silently poisoned a project's remote
    lane). A removal here is a contract event, not a cleanup."""
    assert "shadows_base" in SPEC["attempt"], \
        "attempt.shadows_base dropped from the contract — aba surfaces it"
    assert "repositories" in SPEC["attempt"]
    assert "verified_site" in SPEC["success"]


def test_canonical_success_envelope_passes():
    env = {"satisfied": True, "changed": True,
           "attempts": [
               {"lane": "conda", "outcome": "failed", "seconds": 2.1,
                "error": {"error": "env.solve_failed", "stage": "realize",
                          "detail": "index unreachable", "retryable": True,
                          "hints": {}}},
               {"lane": "pypi", "outcome": "installed", "seconds": 4.0,
                "mutations": ["pylib"], "spelling": "pkgx"}],
           "verified": {"pkgx": {"status": "passed", "check": "import",
                                 "got": "2.1"}},
           "runtime": {"prefix": "/x"}, "session_id": "s1"}
    assert check_envelope(env) == []


def test_canonical_exhaustion_envelope_passes():
    env = {"error": "env.unavailable_in_lanes", "stage": "realize",
           "detail": "no ranked lane could provide the request",
           "retryable": False,
           "hints": {"attempts": [
               {"lane": "conda", "outcome": "refused", "seconds": 0.3,
                "error": {"error": "session.cold_base", "stage": "realize",
                          "detail": "…", "retryable": False, "hints": {}}},
               {"lane": "cran", "outcome": "skipped",
                "skip_reason": "halted"}]}}
    assert check_envelope(env) == []


def test_reader_catches_the_defect_shapes():
    """PROVEN on synthetics: a reader that flags nothing reads as green."""
    bad = {"satisfied": True, "changed": False,
           "attempts": [{"lane": "npm", "outcome": "installed"}],
           "verified": {"x": {"status": "maybe"}}, "runtime": {}}
    out = check_envelope(bad)
    assert any("lane" in p for p in out)
    assert any("changed=false" in p for p in out)
    assert any("maybe" in p for p in out)
    assert check_envelope({"error": "x"}) != []          # hints/stage missing
    assert check_envelope(
        {"satisfied": True, "changed": True, "attempts": [
            {"lane": "cran", "outcome": "failed"}],      # no verbatim error
         "verified": {}, "runtime": {}}) != []
