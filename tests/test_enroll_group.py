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
        "    enabled: true\n"
        f"    root_path: {groups_root}/{{group_dir}}/aba\n"
        '    strip_suffix: ".grp"\n'
        f"    skeleton_template: {skel}\n"
        "credentials:\n"
        # enroll-group only reads group_key_path, but the LAUNCH GATE reads the
        # order — and a site.yaml that omits it resolves no credential at all.
        # Carrying it here keeps the fixture a site the gate would accept.
        "  order: [group_shared]\n"
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


# ── --site defaults to the deployment this copy was installed into ──────────
#
# The default used to be /cluster/aba/site.yaml, which exists on no cluster we
# run on, so every operator had to be told a path — and telling them the WRONG
# lane's path is how a lab gets enrolled into staging and never appears.


@pytest.fixture
def deployed(tmp_path, monkeypatch):
    """A share laid out the way deploy.sh lays it out: <share>/ops/<script>."""
    monkeypatch.delenv("ABA_SITE_CONFIG", raising=False)
    share = tmp_path / "share"
    (share / "ops").mkdir(parents=True)
    (share / "site.yaml").write_text("scopes: {}\n")
    monkeypatch.setattr(eg, "__file__", str(share / "ops" / "enroll-group.py"))
    monkeypatch.setattr(eg, "PORTABLE_SITE", tmp_path / "nowhere" / "site.yaml")
    return share


def test_the_default_site_is_the_one_beside_this_copy(deployed):
    """Staged copy → staged site; production copy → production site. The
    script's own location is the only thing that distinguishes the lanes, and
    it cannot drift the way a hardcoded path can."""
    assert eg.default_site() == deployed / "site.yaml"


def test_an_explicit_setting_still_wins(deployed, tmp_path, monkeypatch):
    elsewhere = tmp_path / "other.yaml"
    elsewhere.write_text("scopes: {}\n")
    monkeypatch.setenv("ABA_SITE_CONFIG", str(elsewhere))
    assert eg.default_site() == elsewhere


def test_a_setting_that_points_nowhere_is_reported_not_silently_replaced(
        deployed, tmp_path, monkeypatch, capsys):
    """If someone set ABA_SITE_CONFIG and it is wrong, say so. Quietly using a
    different site instead is how a group lands in the wrong deployment."""
    monkeypatch.setenv("ABA_SITE_CONFIG", str(tmp_path / "typo.yaml"))
    assert eg.main([REAL_GROUP]) == 2
    assert "typo.yaml" in capsys.readouterr().err


def test_no_deployment_found_asks_rather_than_guessing(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ABA_SITE_CONFIG", raising=False)
    monkeypatch.setattr(eg, "__file__", str(tmp_path / "loose" / "enroll-group.py"))
    monkeypatch.setattr(eg, "PORTABLE_SITE", tmp_path / "nowhere" / "site.yaml")
    assert eg.default_site() is None
    assert eg.main([REAL_GROUP, "--yes"]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "--site" in err


# ── --paste-token: the only credential route a novice can be talked through ─
#
# The other three routes each assume the operator already has the secret in a
# file or is willing to type it after `--oauth-token`, where it lands in the
# shell history and in `ps` for everyone on the login node. This one asks.


def paste(monkeypatch, value, tty=True):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: tty, raising=False)
    monkeypatch.setattr(eg, "_read_masked", lambda prompt="": value)


def drive_prompt(feed):
    """Run the real prompt against a real terminal. Returns (value, on_screen).

    A pty, because the whole behaviour under test IS terminal behaviour —
    raw mode, per-character echo, backspace — none of which a StringIO has."""
    import pty
    import termios
    master, slave = pty.openpty()
    # Take the pty's line discipline OUT of the picture before feeding it.
    # In canonical mode the terminal itself echoes the input, and applies ERASE
    # (0x7f) and KILL (0x15) to its own buffer — so the backspace and ctrl-U
    # tests passed against a build with both handlers deleted: the pty had
    # already done the editing. The fake was doing the work under test.
    attrs = termios.tcgetattr(slave)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)
    termios.tcsetattr(slave, termios.TCSANOW, attrs)
    os.write(master, feed.encode())
    tin = os.fdopen(os.dup(slave), "r", newline="")
    tout = os.fdopen(os.dup(slave), "w")
    saved = (sys.stdin, sys.stdout)
    # A prompt that swallows its input BLOCKS rather than returning something
    # wrong (tty.setraw's default TCSAFLUSH discards the queue, which is
    # exactly that failure). Turn the hang into a test failure, or the guard
    # protects nothing when the suite is run unattended.
    import signal

    def too_slow(*_):
        raise AssertionError("the prompt never returned — it discarded its input")

    old_alarm = signal.signal(signal.SIGALRM, too_slow)
    signal.alarm(5)
    try:
        sys.stdin, sys.stdout = tin, tout
        value = eg._read_masked("paste: ")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_alarm)
        sys.stdin, sys.stdout = saved
        tin.close()
        tout.close()
        os.close(slave)
    shown = os.read(master, 1 << 16).decode(errors="replace")
    os.close(master)
    return value, shown


def test_the_prompt_shows_stars_and_never_the_characters():
    """The feedback the operator asked for — you can see the paste land — with
    the secret still off the screen and out of any scrollback."""
    value, shown = drive_prompt(OAUTH + "\r")
    assert value == OAUTH
    assert OAUTH not in shown
    assert shown.count("*") == len(OAUTH)


def test_a_typo_at_the_prompt_can_be_corrected():
    """Without backspace the only correction is to abandon and start again —
    and the operator cannot see what they are correcting."""
    value, _ = drive_prompt("abc\x7f\x7fZ\r")
    assert value == "aZ"


def test_ctrl_u_clears_the_whole_line():
    value, shown = drive_prompt("wrongpaste\x15" + OAUTH + "\r")
    assert value == OAUTH
    assert "wrongpaste" not in shown


def test_a_pasted_token_enrols_the_lab(site, capsys, monkeypatch):
    paste(monkeypatch, OAUTH)
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 0
    cred = json.loads((site["groups"] / "testlab" / "aba" / ".credentials.json").read_text())
    assert cred == {"claude_code_oauth_token": OAUTH}


def test_the_pasted_secret_never_reaches_the_screen(site, capsys, monkeypatch):
    """A hidden prompt that then ECHOES the value in a confirmation line is
    worse than no hiding at all: the operator believes it was private.

    The feedback the operator does need — did my paste land? — is the LENGTH,
    which is not the secret."""
    paste(monkeypatch, OAUTH)
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 0
    out = capsys.readouterr()
    assert OAUTH not in out.out and OAUTH not in out.err
    assert str(len(OAUTH)) in out.out, "the operator must see that the paste landed"


def test_an_api_key_pasted_goes_in_the_api_key_slot(site, monkeypatch):
    """The operator is not asked which kind it is — they have no way to know.
    Detected from the prefix, because the wrong slot silently degrades the
    lab's auth mode until someone's first launch."""
    paste(monkeypatch, APIKEY)
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 0
    cred = json.loads((site["groups"] / "testlab" / "aba" / ".credentials.json").read_text())
    assert cred == {"anthropic_api_key": APIKEY}


@pytest.mark.parametrize("pasted", [
    "",                                  # hit enter
    "   \n  ",                           # pasted nothing but whitespace
    "ghp_wrongthing",                    # a token, just not ours
    "$ sk-ant-oat01-" + "t" * 20,        # copied the shell prompt too
    "sk-ant-oat01",                      # copied only as far as the prefix
    "Bearer sk-ant-oat01-" + "t" * 20,   # copied it out of a header
])
def test_a_bad_paste_creates_nothing(site, capsys, monkeypatch, pasted):
    """WIDE on purpose: every one of these is a real way a paste goes wrong,
    and each must cost the operator nothing but a retry."""
    before = snapshot(site["groups"])
    paste(monkeypatch, pasted)
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 2
    assert snapshot(site["groups"]) == before
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "What to do" in err, "a refusal without a next step strands this operator"


def test_surrounding_quotes_are_forgiven(site, monkeypatch):
    """Pasting from a place that added quotes is not an error worth a refusal."""
    paste(monkeypatch, f'  "{OAUTH}"  ')
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 0
    cred = json.loads((site["groups"] / "testlab" / "aba" / ".credentials.json").read_text())
    assert cred == {"claude_code_oauth_token": OAUTH}


def test_without_a_terminal_it_refuses_rather_than_echoing(site, capsys, monkeypatch):
    """getpass falls back to VISIBLE input when it cannot get a tty, and warns
    on stderr. For this script that fallback is the failure: the secret appears
    on screen, and in a piped/CI context it would be captured in a log."""
    before = snapshot(site["groups"])
    paste(monkeypatch, OAUTH, tty=False)
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 2
    assert snapshot(site["groups"]) == before
    assert "--cred-file" in capsys.readouterr().err, "name the route that does work"


def test_giving_up_at_the_prompt_is_not_a_crash(site, capsys, monkeypatch):
    """Ctrl-C or Ctrl-D at a password prompt is the commonest thing a nervous
    operator does. It must read as 'nothing happened', not as breakage."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    def bail(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr(eg, "_read_masked", bail)
    before = snapshot(site["groups"])
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes", "--paste-token"]) == 2
    assert snapshot(site["groups"]) == before
    assert "Traceback" not in capsys.readouterr().err


def test_pasting_is_not_consent(site, monkeypatch):
    """The token is collected before the plan is shown, so that the plan can
    say which credential mode the lab will get. Collecting it must not be read
    as agreeing to the plan."""
    paste(monkeypatch, OAUTH)
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    before = snapshot(site["groups"])
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--paste-token"]) == 0
    assert snapshot(site["groups"]) == before


# ── a mode is only evidence where modes are honoured ────────────────────────
#
# Live catch, 2026-08-16: a real enrolment into /groups/tanaka wrote a correct
# credential, printed "readable by: … this filesystem does not appear to honour
# permission bits", and then FAILED validation with "readable by anyone on the
# cluster" — reading as fact the very mode the line above had just explained
# away. Same defect as the setgid check: asserting a mechanism on a filesystem
# that does not implement it.


def enrol(site, *extra):
    return eg.main([REAL_GROUP, "--site", site["cfg"], "--yes",
                    "--oauth-token", OAUTH, *extra])


def test_a_meaningless_mode_is_not_read_as_a_leak(site, capsys, monkeypatch):
    """The live shape, reproduced: the credential reads 0777 AND chmod does
    nothing about it. Enrolment succeeded, so it must say so.

    Built by patching the real os.chmod rather than stubbing the probe, so the
    detection itself stays under test."""
    assert enrol(site) == 0
    cred = site["groups"] / "testlab" / "aba" / ".credentials.json"
    os.chmod(cred, 0o777)                       # what the lab export reports
    monkeypatch.setattr(eg.os, "chmod", lambda *a, **k: None)   # ...and keeps reporting
    capsys.readouterr()
    assert enrol(site) == 0, "a correctly enrolled lab must not be called broken"
    out = capsys.readouterr().out
    assert "readable by anyone" not in out
    assert cred.stat().st_mode & 0o007, "the fake must keep the mode that fooled us"


def test_but_a_real_leak_on_a_real_filesystem_still_fails(site, capsys, monkeypatch):
    """The paired positive — without it the fix above is indistinguishable from
    deleting the check. Here chmod works (so a mode is evidence) and the
    credential really is open to the cluster."""
    real_chmod = os.chmod

    def the_credential_will_not_stay_shut(path, mode, *a, **k):
        return real_chmod(path, 0o777 if str(path).endswith(".credentials.json") else mode)

    monkeypatch.setattr(eg.os, "chmod", the_credential_will_not_stay_shut)
    assert enrol(site) == 3
    assert "readable by anyone" in capsys.readouterr().out


def test_validate_only_looks_at_the_credential_already_there(site, capsys):
    """`--validate-only` carries no credential flag, and used to take that as
    'no credential to check' — reporting a lab healthy without ever opening the
    login it launches with."""
    assert enrol(site) == 0
    (site["groups"] / "testlab" / "aba" / ".credentials.json").write_text("{}\n")
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--validate-only"]) == 3
    assert "does not contain a login" in capsys.readouterr().out


def test_the_probe_tells_the_two_filesystems_apart(tmp_path, monkeypatch):
    assert eg._honours_mode_bits(tmp_path) is True
    monkeypatch.setattr(eg.os, "chmod", lambda *a, **k: None)
    assert eg._honours_mode_bits(tmp_path) is False


def test_the_probe_leaves_nothing_behind(tmp_path, monkeypatch):
    before = snapshot(tmp_path)
    eg._honours_mode_bits(tmp_path)
    monkeypatch.setattr(eg.os, "chmod", lambda *a, **k: None)
    eg._honours_mode_bits(tmp_path)
    assert snapshot(tmp_path) == before


# ── validation is ARMED: it must be able to FAIL ───────────────────────────

def _secondary_gid():
    """A group we belong to that is NOT our primary.

    The default fixture makes the lab gid == our own primary gid so chown can
    succeed — which quietly destroys the distinction the sharing check exists to
    make: with lab == primary, EVERY new file "inherits the lab group", setgid
    or not, and the check can never fail. A fake that cannot express the failure
    blesses it."""
    primary = os.getgid()
    for gid in os.getgroups():
        if gid != primary:
            return gid
    pytest.skip("need a secondary unix group to distinguish inherited ownership")


@pytest.fixture
def lab_is_a_real_other_group(site, monkeypatch):
    gid = _secondary_gid()

    class FakeGr:
        gr_gid = gid

    def fake_getgrnam(name):
        if name != REAL_GROUP:
            raise KeyError(name)
        return FakeGr()
    monkeypatch.setattr(eg.grp, "getgrnam", fake_getgrnam)
    return site


def test_validate_only_fails_on_a_half_enrolled_workspace(lab_is_a_real_other_group):
    """A workspace owned by the lab but not SHARING with it must not validate.

    On a filesystem that honours modes, "not sharing" means: no setgid, so a
    file created here comes out under the creator's own primary group and the
    rest of the lab cannot write it."""
    site = lab_is_a_real_other_group
    root = site["groups"] / "testlab" / "aba"
    root.mkdir(parents=True)
    (root / ".aba-workspace").touch()
    os.chown(root, -1, _secondary_gid())     # owned by the lab...
    os.chmod(root, 0o775)                    # ...but NOT setgid: nothing inherits
    rc = eg.main([REAL_GROUP, "--site", site["cfg"], "--validate-only"])
    assert rc == 3, "a workspace the lab cannot share is not a valid enrolment"


def test_validate_only_passes_once_new_files_reach_the_lab(lab_is_a_real_other_group):
    """The other side of the same check: turning sharing ON must flip it green.

    Paired with the test above so the check is shown to DISCRIMINATE rather than
    just to fail — a validator that always says no is as useless as one that
    always says yes."""
    site = lab_is_a_real_other_group
    root = site["groups"] / "testlab" / "aba"
    root.mkdir(parents=True)
    (root / ".aba-workspace").touch()
    os.chown(root, -1, _secondary_gid())
    os.chmod(root, 0o2775)                   # setgid: new files reach the lab
    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--validate-only"]) == 0


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


# ── the seam: enrol writes, the launch gate reads ────────────────────────────

def test_the_enrolled_lab_passes_the_launch_gate(site, tmp_path, monkeypatch):
    """Enrolment's whole purpose is to make aba_preflight say yes.

    Every guard above tests enroll-group alone, against its own idea of what it
    wrote. That is one door. This feeds its output into the door that consumes
    it — the launch gate — because the two read the same site.yaml through
    different expanders, and a lab that enrols perfectly and still fails to
    launch is indistinguishable, to the operator, from not being enrolled."""
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "aba_preflight_seam", ROOT / "install" / "ood" / "aba_preflight.py")
    pf = _ilu.module_from_spec(spec)
    sys.modules["aba_preflight_seam"] = pf
    spec.loader.exec_module(pf)

    assert eg.main([REAL_GROUP, "--site", site["cfg"], "--yes",
                    "--oauth-token", OAUTH]) == 0

    staged = tmp_path / "staged"; staged.mkdir()
    for k, v in {"ABA_SITE_CONFIG": site["cfg"], "ABA_PF_GROUP": REAL_GROUP,
                 "ABA_PF_USER": "alice", "ABA_PF_HOME": str(tmp_path / "home"),
                 "ABA_PF_STAGED": str(staged)}.items():
        monkeypatch.setenv(k, v)
    pf.main()

    import yaml
    st = yaml.safe_load((staged / "status.yaml").read_text())
    assert st["ready"] is True, st.get("blocked_on")
    assert st["scopes"]["group"]["state"] == "ok"
    assert st["credentials"]["resolved"] is True

    # and the credential the gate hands the session is the one enrolment wrote,
    # in the slot that keeps it an OAuth bearer rather than a downgraded key
    env = (staged / "aba-env.sh").read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN=" in env
    assert "ANTHROPIC_API_KEY=" not in env
    # never echoed to the terminal, only to the 0600 env file
    assert OAUTH not in (staged / "status.yaml").read_text()
