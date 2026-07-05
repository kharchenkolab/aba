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


# ── ENVS_DIR shared-FS gate (finding F6b): fstype classifier is empirical,
#    not path-prefix — no fixtures needed, so it also runs standalone. ──
def test_fs_kind_classifier_returns_valid_kind():
    from aba_installer import cli
    kind, fstype = cli._fs_kind_for_path("/")
    assert kind in ("shared", "node_local", "unknown")
    assert fstype is None or isinstance(fstype, str)


def test_fs_kind_classifier_sets_cover_common_fstypes():
    from aba_installer import cli
    assert {"nfs", "lustre", "beegfs", "gpfs"} <= cli._SHARED_FS
    assert {"tmpfs", "ext4", "xfs", "overlay"} <= cli._LOCAL_FS
