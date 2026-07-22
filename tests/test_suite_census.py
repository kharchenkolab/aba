"""Every test file is accounted for — gated, deliberately excluded, or legacy.

CI runs ONLY the files listed in scripts/run_guard_tests.sh. A tests/test_*.py
that no lane runs is silent rot: this week alone three such files were found
stale or silently broken (test_r_install_lanes.py was red on main for days — its
stub predated a contract change and nothing noticed; test_live_session_smallfixes
and test_isolated_env_tools were never enforced at all).

The census: every tests/test_*.py must be exactly one of
  - GATED     — listed in scripts/run_guard_tests.sh;
  - EXCLUDED  — in the annotated allowlist below, with a rationale (audited,
                deliberately not in the hermetic CI lane);
  - LEGACY    — in tests/_census_ungated_legacy.txt, the frozen 2026-07-22
                snapshot of the pre-census backlog. The list may only SHRINK:
                gate a file or move it to EXCLUDED, then delete its line.

A NEW test file that lands in none of the three fails this census in the same
commit that adds it — gating is part of shipping a test, not a later chore.
"""
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = ROOT / "scripts" / "run_guard_tests.sh"
LEGACY_FILE = ROOT / "tests" / "_census_ungated_legacy.txt"

pytestmark = pytest.mark.platform

# Audited, deliberately outside the hermetic CI lane — each entry carries the
# reason it cannot (or must not) run there. Growth of this list is a REVIEWED
# act: an entry without a real rationale belongs in the gated suite instead.
EXCLUDED: dict[str, str] = {
    # (empty at census introduction — the backlog is in the legacy snapshot;
    #  entries move here only after an explicit audit says "never gate this")
}


def _gated() -> set[str]:
    src = GATE_SCRIPT.read_text()
    return set(re.findall(r"tests/test_[a-z0-9_]+\.py", src))


def _legacy() -> list[str]:
    return [ln.strip() for ln in LEGACY_FILE.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")]


def _present() -> set[str]:
    return {f"tests/{p.name}" for p in (ROOT / "tests").glob("test_*.py")}


def census(present: set[str], gated: set[str], legacy: list[str],
           excluded: set[str]) -> list[str]:
    """Pure rule → violations. Tested on synthetic inputs below (a scanner
    that matches nothing reads as green), then applied to the real repo."""
    problems: list[str] = []
    legacy_set = set(legacy)
    for f in sorted(present - gated - legacy_set - excluded):
        problems.append(
            f"UNACCOUNTED: {f} — new test files must be added to "
            f"scripts/run_guard_tests.sh in the same commit (or, after audit, "
            f"to the EXCLUDED allowlist with a rationale)")
    # the ratchet: legacy may only shrink — a gated/deleted/excluded file's
    # line must be pruned, so the backlog number is always honest
    for f in sorted(legacy_set & gated):
        problems.append(f"STALE LEGACY: {f} is now gated — remove its line "
                        f"from {LEGACY_FILE.name}")
    for f in sorted(legacy_set - present):
        problems.append(f"STALE LEGACY: {f} no longer exists — remove its "
                        f"line from {LEGACY_FILE.name}")
    for f in sorted(legacy_set & excluded):
        problems.append(f"DOUBLE-BOOKED: {f} is in both the legacy snapshot "
                        f"and EXCLUDED — keep exactly one")
    for f in sorted(set(excluded) & gated):
        problems.append(f"DOUBLE-BOOKED: {f} is both gated and EXCLUDED")
    dupes = {f for f in legacy_set if legacy.count(f) > 1}
    for f in sorted(dupes):
        problems.append(f"DUPLICATE legacy line: {f}")
    return problems


# ── the scanner is proven on synthetic inputs first ─────────────────────────

def test_census_catches_a_new_ungated_file():
    out = census({"tests/test_a.py", "tests/test_new.py"},
                 {"tests/test_a.py"}, [], set())
    assert len(out) == 1 and "UNACCOUNTED: tests/test_new.py" in out[0]


def test_census_ratchet_flags_gated_and_deleted_legacy_lines():
    out = census({"tests/test_a.py"}, {"tests/test_a.py"},
                 ["tests/test_a.py", "tests/test_gone.py"], set())
    assert any("test_a.py is now gated" in p for p in out)
    assert any("test_gone.py no longer exists" in p for p in out)


def test_census_flags_double_booking_and_dupes():
    out = census({"tests/test_a.py", "tests/test_b.py"},
                 {"tests/test_a.py"},
                 ["tests/test_b.py", "tests/test_b.py"],
                 {"tests/test_b.py", "tests/test_a.py"})
    assert any("DOUBLE-BOOKED: tests/test_b.py" in p for p in out)
    assert any("DOUBLE-BOOKED: tests/test_a.py is both gated" in p for p in out)
    assert any("DUPLICATE" in p for p in out)


def test_census_accepts_a_fully_accounted_tree():
    assert census({"tests/test_a.py", "tests/test_b.py", "tests/test_c.py"},
                  {"tests/test_a.py"}, ["tests/test_b.py"],
                  {"tests/test_c.py"}) == []


# ── and then applied to the real repo ───────────────────────────────────────

def test_every_test_file_is_accounted_for():
    problems = census(_present(), _gated(), _legacy(), set(EXCLUDED))
    assert not problems, "\n".join(problems)


def test_the_census_is_armed():
    """A census over an empty or unreadable universe proves nothing."""
    assert len(_present()) > 100, "test enumeration returned implausibly few"
    assert len(_gated()) > 10, "gate-script parse returned implausibly few"
    assert len(_legacy()) > 0 or not (set(_present()) - _gated() - set(EXCLUDED))
