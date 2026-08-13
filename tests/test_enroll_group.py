"""enroll-group: the operator is a lab member, once, possibly never in a terminal.

That audience decides what these guards assert. The script is the pilot gate —
it is what makes a lab appear on the launch form — and the person running it
cannot debug it, cannot read a traceback, and will not run it again to check.
So the properties under test are:

  * nothing is created until the plan has been shown and agreed;
  * a mistyped group name costs NOTHING (the old script created
    /groups/<typo>/aba and then printed "✓ enrolled");
  * a credential in the wrong slot is refused rather than silently written,
    because the wrong slot degrades the auth mode and nobody finds out until a
    user's first launch;
  * no failure path ever shows a Python traceback;
  * a secret never reaches the terminal.

The assertions are on the ACTION — filesystem state and exit code — not on the
wording. A test that only checked for an error string would have passed against
the old script, which printed a `note:` about the missing group AND created the
folder anyway.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "aba_enroll_group", ROOT / "install" / "ood" / "enroll_group.py")
eg = importlib.util.module_from_spec(_spec)
sys.modules["aba_enroll_group"] = eg
_spec.loader.exec_module(eg)

pytestmark = pytest.mark.platform

REAL_GROUP = "testlab.grp"
OAUTH = "sk-ant-oat01-" + "t" * 20
APIKEY = "sk-ant-api03-" + "k" * 20


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A miniature site: config, skeleton, and a group root we can write.

    The fake group database REFUSES an unknown name, exactly as grp does — a
    permissive fake here would bless the very bug these tests exist for."""
    groups_root = tmp_path / "groups"
    groups_root.mkdir()
    # The lab's shared folder already exists in production — the storage admins
    # create /groups/<lab>; ABA only ever adds the `aba` subdir inside it.
    (groups_root / "testlab").mkdir()
    skel = tmp_path / "skeleton"
    (skel / "bundle" / "rules").mkdir(parents=True)
    (skel / ".aba-workspace").touch()

    cfg = tmp_path / "site.yaml"
    cfg.write_text(
        "scopes:\n"
        "  group:\n"
        f"    root_path: {groups_root}/{{group_dir}}/aba\n"
        '    strip_suffix: ".grp"\n'
        f"    skeleton_template: {skel}\n"
        "credentials:\n"
        f"  group_key_path: {groups_root}/{{group_dir}}/aba/.credentials.json\n")

    class FakeGr:
        def __init__(self, gid):
            self.gr_gid = gid

    def fake_getgrnam(name):
        if name != REAL_GROUP:
            raise KeyError(name)          # refuses, like the real thing
        return FakeGr(os.getgid())        # a gid we can actually chown to

    monkeypatch.setattr(eg.grp, "getgrnam", fake_getgrnam)
    return {"cfg": str(cfg), "groups": groups_root, "skel": skel}


def snapshot(root: Path):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def run(site, *argv, stdin_tty=True, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: stdin_tty, raising=False)
    return eg.main([*argv, "--site", site["cfg"]])


# ── the load-bearing one ────────────────────────────────────────────────────

def test_typo_in_group_name_creates_nothing(site, capsys):
    """The most likely operator error. It must cost nothing at all."""
    before = snapshot(site["groups"])
    rc = eg.main(["tanka.grp", "--site", site["cfg"], "--yes"])
    assert rc == 2
    assert snapshot(site["groups"]) == before, \
        "a mistyped group name must not create any directory"
    err = capsys.readouterr().err
    assert "no unix group" in err and "groups" in err   # names the fix


def test_typo_is_caught_before_the_confirmation(site, capsys):
    """Refusal comes first, so the operator is never asked to approve nonsense."""
    rc = eg.main(["tanka.grp", "--site", site["cfg"], "--dry-run"])
    assert rc == 2
    assert "about to do" not in capsys.readouterr().out


# ── consent ────────────────────────────────────────────────────────────────

def test_dry_run_changes_nothing(site, capsys):
    before = snapshot(site["groups"])
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--dry-run"])
    assert rc == 0
    assert snapshot(site["groups"]) == before
    assert "Nothing was changed" in capsys.readouterr().out


def test_declining_changes_nothing(site, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    before = snapshot(site["groups"])
    rc = eg.main([REAL_GROUP, "--site", site["cfg"]])
    assert rc == 0
    assert snapshot(site["groups"]) == before


def test_non_interactive_without_yes_refuses(site, monkeypatch):
    """No tty and no --yes: refuse rather than assume consent."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    before = snapshot(site["groups"])
    rc = eg.main([REAL_GROUP, "--site", site["cfg"]])
    assert rc == 2
    assert snapshot(site["groups"]) == before


def test_preview_names_the_resolved_values(site, capsys):
    """Otherwise 'here is what I'll do' can drift from what it then does."""
    eg.main([REAL_GROUP, "--site", site["cfg"], "--dry-run"])
    out = capsys.readouterr().out
    assert str(site["groups"] / "testlab" / "aba") in out    # strip_suffix applied
    assert REAL_GROUP in out


# ── enrolment, idempotence, rotation ───────────────────────────────────────

def test_enrols_and_validates(site, capsys):
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes"])
    assert rc == 0, capsys.readouterr().out
    root = site["groups"] / "testlab" / "aba"
    assert (root / ".aba-workspace").exists()
    assert root.stat().st_mode & 0o2000, "setgid must be set so the lab shares it"


def test_rerun_preserves_the_original_enrolment_date(site):
    eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--by", "first"])
    stamp = site["groups"] / "testlab" / "aba" / ".aba-workspace"
    first = dict(l.split(":", 1) for l in stamp.read_text().splitlines()
                 if ":" in l and not l.startswith("#"))
    eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--by", "second",
             "--oauth-token", OAUTH])
    second = dict(l.split(":", 1) for l in stamp.read_text().splitlines()
                  if ":" in l and not l.startswith("#"))
    assert second["enrolled_at"] == first["enrolled_at"], \
        "rotating a credential must not rewrite the original enrolment date"
    assert second["enrolled_by"] == first["enrolled_by"]
    assert "updated_at" in second, "a re-run should be recorded as an update"


def test_foreign_folder_is_refused(site):
    root = site["groups"] / "testlab" / "aba"
    root.mkdir(parents=True)
    (root / "someone-elses-data.txt").write_text("x")
    before = snapshot(site["groups"])
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes"])
    assert rc == 2
    assert snapshot(site["groups"]) == before


# ── credentials ────────────────────────────────────────────────────────────

def test_credential_in_the_wrong_slot_is_refused(site, capsys):
    """An api key passed as --oauth-token used to be written without complaint."""
    before = snapshot(site["groups"])
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--oauth-token", APIKEY])
    assert rc == 2
    assert snapshot(site["groups"]) == before
    assert "api-key" in capsys.readouterr().err     # points at the right flag


def test_malformed_cred_file_is_refused(site, tmp_path):
    bad = tmp_path / "cred.json"
    bad.write_text("this is not json")
    before = snapshot(site["groups"])
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--cred-file", str(bad)])
    assert rc == 2
    assert snapshot(site["groups"]) == before


def test_cred_file_without_a_known_key_is_refused(site, tmp_path):
    bad = tmp_path / "cred.json"
    bad.write_text(json.dumps({"token": "sk-ant-oat01-xxx"}))
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--cred-file", str(bad)])
    assert rc == 2


def test_credential_is_private_and_never_printed(site, capsys):
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--oauth-token", OAUTH])
    assert rc == 0
    cred = site["groups"] / "testlab" / "aba" / ".credentials.json"
    assert cred.stat().st_mode & 0o007 == 0, "the shared login must not be cluster-readable"
    captured = capsys.readouterr()
    assert OAUTH not in captured.out and OAUTH not in captured.err, \
        "a secret must never reach the terminal"
    assert OAUTH not in (site["groups"] / "testlab" / "aba" / ".aba-workspace").read_text()


def test_the_shared_login_is_readable_by_the_LAB(site):
    """The bug this pins: 0600 made the "lab-shared" credential readable by
    exactly one person — whoever ran the script.

    aba_preflight reads this file AS THE LAUNCHING USER, and its read_cred_file
    swallows every exception, so a PermissionError is indistinguishable from
    "no credential configured". Every other member of the lab would silently
    get no login while the enroller tested it and saw it work. Two-sided on
    purpose: readable by the group, never by the rest of the cluster."""
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes",
                    "--oauth-token", OAUTH]) == 0
    st = (site["groups"] / "testlab" / "aba" / ".credentials.json").stat()
    assert st.st_mode & 0o040, "the lab must be able to read its own shared login"
    assert st.st_mode & 0o007 == 0, "but the rest of the cluster must not"


# ── validation is ARMED: it must be able to FAIL ───────────────────────────

def test_validate_only_fails_on_a_half_enrolled_workspace(site):
    root = site["groups"] / "testlab" / "aba"
    root.mkdir(parents=True)
    (root / ".aba-workspace").touch()        # marker, but never shared with the lab
    os.chmod(root, 0o755)                    # no setgid
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--validate-only"])
    assert rc == 3, "a workspace the lab cannot share is not a valid enrolment"


def test_validate_only_passes_on_a_good_one(site):
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes"]) == 0
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--validate-only"]) == 0


def test_ownership_failure_is_not_reported_as_success(site, monkeypatch):
    """chgrp failing means the lab cannot collaborate — that is not a success.

    Faithful to the real case: the operator is not a member of the target
    group, so chown raises and the directory keeps its original gid. Only
    `chown` is patched — patching chmod too would break the skeleton copy and
    test something else entirely."""
    other_gid = 65534                      # a gid we are definitely not

    class NotOurs:
        gr_gid = other_gid
    monkeypatch.setattr(eg.grp, "getgrnam",
                        lambda n: NotOurs() if n == REAL_GROUP
                        else (_ for _ in ()).throw(KeyError(n)))

    def refuse_chown(*_a, **_k):
        raise PermissionError(13, "Operation not permitted")
    monkeypatch.setattr(eg.os, "chown", refuse_chown)

    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--yes"])
    assert rc == 3, "an unshareable workspace must not exit 0"


# ── properties over EVERY failure path ─────────────────────────────────────

FAILURE_CASES = [
    pytest.param(["nosuchgroup.grp", "--yes"], id="unknown-group"),
    pytest.param([REAL_GROUP, "--yes", "--oauth-token", APIKEY], id="wrong-slot"),
    pytest.param([REAL_GROUP, "--yes", "--cred-file", "/nonexistent/x.json"], id="missing-cred-file"),
]


@pytest.mark.parametrize("argv", FAILURE_CASES)
def test_no_failure_path_shows_a_traceback(site, capsys, argv):
    rc = eg.main([*argv, "--site", site["cfg"]])
    assert rc == 2
    captured = capsys.readouterr()
    assert "Traceback (most recent call last)" not in (captured.err + captured.out)


@pytest.mark.parametrize("argv", FAILURE_CASES)
def test_every_refusal_says_what_to_do(site, capsys, argv):
    eg.main([*argv, "--site", site["cfg"]])
    assert "What to do:" in capsys.readouterr().err


def test_missing_site_config_is_a_sentence_not_a_traceback(tmp_path, capsys):
    rc = eg.main(["anygroup", "--site", str(tmp_path / "absent.yaml"), "--yes"])
    assert rc == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "What to do:" in captured.err


# ── degenerate shapes ──────────────────────────────────────────────────────

def test_group_without_the_site_suffix_is_a_no_op(tmp_path, monkeypatch):
    """Sites have no-suffix groups too; strip_suffix must not mangle them."""
    groups_root = tmp_path / "groups"; groups_root.mkdir()
    (groups_root / "berger").mkdir()
    cfg = tmp_path / "site.yaml"
    cfg.write_text("scopes:\n  group:\n"
                   f"    root_path: {groups_root}/{{group_dir}}/aba\n"
                   '    strip_suffix: ".grp"\n')

    class FakeGr:
        gr_gid = os.getgid()
    monkeypatch.setattr(eg.grp, "getgrnam",
                        lambda n: FakeGr() if n == "berger" else (_ for _ in ()).throw(KeyError(n)))
    assert eg.main(["berger", "--site", str(cfg), "--yes"]) == 0
    assert (groups_root / "berger" / "aba" / ".aba-workspace").exists()


def test_site_without_group_scope_refuses_cleanly(tmp_path, monkeypatch, capsys):
    """A bare site.yaml falls back to /groups/{group}/aba. That path will not
    exist here, so a REFUSAL is correct — what must never happen is a crash."""
    cfg = tmp_path / "site.yaml"
    cfg.write_text("{}\n")

    class FakeGr:
        gr_gid = os.getgid()
    monkeypatch.setattr(eg.grp, "getgrnam", lambda n: FakeGr())
    rc = eg.main([REAL_GROUP, "--site", str(cfg), "--dry-run"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "What to do:" in err


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_placeholder_this_tool_cannot_expand_is_refused(tmp_path, monkeypatch, capsys):
    """aba_preflight's expander knows {user} and {home}; this one does not.

    Rather than keep a second, half-complete copy of that vocabulary, enrolment
    refuses a path it cannot fully resolve. Silently creating a directory
    literally named "{user}" while preflight looks elsewhere would leave the lab
    invisible with nothing to explain why."""
    groups_root = tmp_path / "groups"; groups_root.mkdir()
    cfg = tmp_path / "site.yaml"
    cfg.write_text("scopes:\n  group:\n"
                   f"    root_path: {groups_root}/{{user}}/{{group_dir}}/aba\n"
                   '    strip_suffix: ".grp"\n')

    class FakeGr:
        gr_gid = os.getgid()
    monkeypatch.setattr(eg.grp, "getgrnam", lambda n: FakeGr())
    before = snapshot(groups_root)
    rc = eg.main([REAL_GROUP, "--site", str(cfg), "--yes"])
    assert rc == 2
    assert snapshot(groups_root) == before, "must not create a literal {user} directory"
    err = capsys.readouterr().err
    assert "Traceback" not in err and "What to do:" in err
