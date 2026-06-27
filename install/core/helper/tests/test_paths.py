"""H1 — path resolution under ABA_HOME override."""
from pathlib import Path

from aba_installer import paths


def test_aba_home_respects_env_override(tmp_aba_home):
    assert paths.aba_home() == tmp_aba_home


def test_installer_dir_is_created():
    d = paths.installer_dir()
    assert d.is_dir()
    assert d.name == "installer"


def test_logs_dir_is_created():
    d = paths.logs_dir()
    assert d.is_dir()
    assert d.name == "logs"


def test_subdirs_resolve_relative_to_aba_home(tmp_aba_home):
    assert paths.env_dir() == tmp_aba_home / "env"
    assert paths.repo_dir() == tmp_aba_home / "repo"
    assert paths.runtime_dir() == tmp_aba_home / "runtime"
    assert paths.config_env() == tmp_aba_home / "config.env"
    assert paths.port_file() == tmp_aba_home / "installer" / "port.txt"
