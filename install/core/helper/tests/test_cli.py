"""Headless installer CLI (cli.py): doctor + the headless playbook runner."""
import pytest


def test_doctor_reports_missing_on_empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from aba_installer import cli
    assert cli.main(["doctor"]) == 1            # nothing installed → non-zero


def test_install_only_preflight_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from aba_installer import cli
    assert cli.main(["install", "--only", "preflight"]) == 0   # preflight is read-only


def test_unknown_step_only_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from aba_installer import cli
    # only a non-existent step → no steps run → treated as not-complete (rc 1)
    assert cli.main(["install", "--only", "nope"]) == 1
