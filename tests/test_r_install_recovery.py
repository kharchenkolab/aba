"""Version-aware R capability install + recovery diagnostics (sccore-upgrade trap)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.exec.r import version_ge, parse_version_requirement, install_command


def test_version_ge():
    assert version_ge("1.1.0", "1.1.0")
    assert version_ge("1.2.0", "1.1.0")
    assert version_ge("2.0", "1.9.9")
    assert not version_ge("1.0.7", "1.1.0")     # the actual sccore trap
    assert not version_ge(None, "1.1.0")        # absent → needs install


def test_parse_version_requirement():
    cases = [
        'Error: utils::packageVersion("sccore") >= "1.1.0" is not TRUE',
        "namespace ‘sccore’ 1.0.7 is already loaded, but >= 1.1.0 is required",
        "package 'sccore' 1.0.7 was found, but >= 1.1.0 is required",
    ]
    for t in cases:
        assert parse_version_requirement(t) == {"package": "sccore", "min_version": "1.1.0"}, t
    assert parse_version_requirement("some unrelated compile error") is None
    assert parse_version_requirement("") is None


def test_install_command_force():
    forced = install_command("github", "kharchenkolab/sccore", lib="/tmp/lib", ref="dev", force=True)
    assert "install_github" in forced and "force=TRUE" in forced and "upgrade='always'" in forced
    plain = install_command("github", "kharchenkolab/sccore", lib="/tmp/lib", ref="dev", force=False)
    assert "force=TRUE" not in plain and "upgrade='never'" in plain
