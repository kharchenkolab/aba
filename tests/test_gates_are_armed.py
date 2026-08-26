"""A gate whose corpus is drawn from what already works cannot fail.

This is the dominant defect class in this repo's instruments, and it has now
produced the same outcome three times:

  live_install_probe   `--install` passed `--pack-provided-only`, so it asked
                       only for libraries the pack already ships. 46/46
                       `ready_from_pack` read as a green install gate while the
                       install path had never run. It let through an isolated
                       env with no C++ compiler and a cran toolchain with no
                       libxml2 headers — both found by a user, immediately.
  live_audit           walked the projects that EXIST on a throwaway home, and
                       printed "every advertised surface answers honestly"
                       under "audited 0 project(s)".
  live_surface_probe   "every produced output is advertised, unique, servable,
                       and substrate-executed" is vacuously true of zero
                       outputs.

The shared shape: the pass message is a universally-quantified claim, and an
empty subject set satisfies it. The fix is always the same — count the
subjects and refuse to pass on zero.

This file is the PROPERTY guard, not another instance fix: a new probe that
can pass having measured nothing fails here without anyone remembering to
write a test for it.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "regtest" / "harness"

# Probes that run a corpus against a live server and return an exit code.
# A probe added here without an arming check fails this file.
CORPUS_PROBES = [
    "live_install_probe.py",
    "live_surface_probe.py",
]

_ARMING = ("unarmed", "UNARMED")


def test_every_corpus_probe_can_detect_an_empty_corpus():
    missing = []
    for name in CORPUS_PROBES:
        src = (HARNESS / name).read_text()
        if not any(tok in src for tok in _ARMING):
            missing.append(name)
    assert not missing, (
        f"{missing} can report success having checked nothing — every "
        f"corpus probe must count its subjects and refuse to pass on zero")


def test_the_arming_check_gates_the_exit_code():
    """Printing a warning is not gating. The 'audited 0 project(s)' line was
    printed directly under a green summary for weeks."""
    for name in CORPUS_PROBES:
        src = (HARNESS / name).read_text()
        rets = [ln for ln in src.splitlines()
                if "return 1" in ln or "return 1 if" in ln]
        gated = any(tok in ln for ln in rets for tok in _ARMING)
        # either the exit expression names it, or an early `return 1` sits
        # inside the arming branch
        if not gated:
            i = min(src.index(t) for t in _ARMING if t in src)
            gated = "return 1" in src[i:i + 700]
        assert gated, f"{name}: the arming check does not affect the exit code"


def test_a_universal_pass_message_names_its_subject_count():
    """"every X is fine" with no count is the sentence that keeps lying. If a
    probe claims something about all of its subjects, the number has to be in
    the sentence — a reader seeing `0 produced output(s)` stops; a reader
    seeing `PASS — every produced output ...` does not."""
    src = (HARNESS / "live_surface_probe.py").read_text()
    passline = src[src.index('print(f"PASS'):][:200]
    assert "{checked}" in passline, (
        "the PASS line must carry the subject count it is quantifying over")


def test_the_install_gate_default_scope_can_install():
    """The arming check only bites if the DEFAULT scope is capable of
    installing. `--install` defaulting to pack-provided-only satisfied every
    structural check above and still tested nothing."""
    vsh = REPO.parent / "aba-vbc" / "verify.sh"
    if not vsh.exists():
        pytest.skip("aba-vbc checkout not alongside this one")
    assert "--install) INSTALL=mixed" in vsh.read_text()
