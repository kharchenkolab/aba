"""Platform-modularity invariants as pytest cases (so `pytest` runs them too, in
addition to the CI invariants workflow). modularity2.md §8 columns."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*cmd):
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)


def test_seam_invariant():
    r = _run("bash", "scripts/check_seam.sh")
    assert r.returncode == 0, r.stdout + r.stderr


def test_platform_purity_invariant():
    r = _run(sys.executable, "tests/check_platform_purity.py")
    assert r.returncode == 0, r.stdout + r.stderr


def test_derivation_invariant():
    r = _run(sys.executable, "tests/check_derivation.py")
    assert r.returncode == 0, r.stdout + r.stderr

# The access-gate invariant (no ungated entity mutation) lives in its own,
# more-thorough test: tests/test_project_pinning_coverage.py (all mutating
# routes + bio routes + an exemption table). Not duplicated here.
