"""SSH access & trust helpers (misc/compute_settings.md §5.2) — pure
classification/parsing plus filesystem-only key/TOFU behavior. No network:
subprocess entry points are monkeypatched."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

from core.compute import preflight as pf  # noqa: E402


# ── classification ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("rc,stderr,expect", [
    (0, "", "ok"),
    (255, "me@host: Permission denied (publickey,password).", "auth"),
    (255, "Host key verification failed.", "hostkey"),
    (255, "No ED25519 host key is known for login.x and you have requested strict checking.", "hostkey"),
    (255, "ssh: Could not resolve hostname nowhere.example: Name or service not known", "dns"),
    (255, "connect to host 10.0.0.1 port 22: Connection refused", "network"),
    (255, "connect to host x port 22: Operation timed out", "network"),
    (255, "some exotic failure", "unknown"),
])
def test_classify(rc, stderr, expect):
    assert pf.classify(rc, stderr) == expect


def test_causes_speak_plainly():
    assert "VPN" in pf.CAUSE["network"]
    assert "password" in pf.CAUSE["auth"]


# ── target validation ────────────────────────────────────────────────────────

def test_validate_rejects_hostile_targets():
    assert pf.validate_target("host; rm -rf /", []) is not None
    assert pf.validate_target("me@ok@twice", []) is not None
    assert pf.validate_target("me@login.vbc.ac.at",
                              ["-o", "ProxyCommand=evil"]) is not None
    assert pf.validate_target("me@login.vbc.ac.at",
                              ["-o", "ConnectTimeout=5"]) is None


# ── ssh-config parsing ───────────────────────────────────────────────────────

def test_parse_ssh_config_concrete_hosts_only():
    hosts = pf.parse_ssh_config(
        "Host vbc\n  HostName login.vbc.ac.at\n  User me\n\n"
        "Host *.edu\n  User other\n\n"
        "Host box jump1\n  Port 2222\n  ProxyJump me@gw.lab\n")
    assert {"host": "vbc", "hostname": "login.vbc.ac.at", "user": "me"} in hosts
    assert all(h["host"] != "*.edu" for h in hosts)
    box = next(h for h in hosts if h["host"] == "box")
    assert box["port"] == "2222" and box["jump"] == "me@gw.lab"


# ── TOFU host keys ───────────────────────────────────────────────────────────

import base64  # noqa: E402

KEYLINE = ("login.vbc.ac.at ssh-ed25519 "
           + base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519" + b"k" * 32).decode())


def test_fingerprint_of_keyline():
    out = pf._fingerprint_of(KEYLINE)
    assert out["keytype"] == "ssh-ed25519"
    assert out["fingerprint"].startswith("SHA256:")


def test_accept_hostkey_appends_once(tmp_path, monkeypatch):
    store = tmp_path / "state" / "ssh_known_hosts"
    monkeypatch.setattr(pf, "known_hosts_path", lambda: store)
    p1 = pf.accept_hostkey(KEYLINE)
    p2 = pf.accept_hostkey(KEYLINE)
    assert p1 == p2 == store
    assert store.read_text().count(KEYLINE) == 1


def test_trust_opts_consult_both_stores(tmp_path, monkeypatch):
    store = tmp_path / "ssh_known_hosts"
    monkeypatch.setattr(pf, "known_hosts_path", lambda: store)
    opts = pf.trust_opts()
    joined = " ".join(opts)
    assert "~/.ssh/known_hosts" in joined and str(store) in joined
    assert "StrictHostKeyChecking=yes" in joined


# ── key setup: the no-password contract ──────────────────────────────────────

def test_keysetup_generates_once_and_never_takes_a_password(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    calls = []

    def fake_run(argv, timeout=20):
        calls.append(argv)
        Path(argv[argv.index("-f") + 1]).write_text("KEY")
        Path(argv[argv.index("-f") + 1] + ".pub").write_text("PUB")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(pf, "_run", fake_run)
    out1 = pf.keysetup("me@login.vbc.ac.at")
    assert out1["ok"] and out1["created"]
    assert out1["command"] == \
        f"ssh-copy-id -i {tmp_path}/.ssh/aba_ed25519.pub me@login.vbc.ac.at"
    # the API has no way to carry a secret: generation is -N '' and the
    # install command is handed BACK to the user, not executed
    assert ["-N", ""] == [calls[0][calls[0].index("-N")],
                          calls[0][calls[0].index("-N") + 1]]
    out2 = pf.keysetup("me@login.vbc.ac.at", port=2222)
    assert not out2["created"] and len(calls) == 1     # idempotent
    assert out2["command"].startswith("ssh-copy-id -i ")
    assert "-p 2222" in out2["command"]


# ── preflight + remote facts (ssh monkeypatched) ─────────────────────────────

def _cp(rc: int, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], rc, out, err)


def test_preflight_attaches_fingerprint_on_first_contact(monkeypatch):
    monkeypatch.setattr(pf, "_ssh",
                        lambda *a, **k: _cp(255, "", "Host key verification failed."))
    monkeypatch.setattr(pf, "scan_hostkey",
                        lambda dest, port=None: {"line": KEYLINE,
                                                 "fingerprint": "SHA256:abc",
                                                 "keytype": "ssh-ed25519"})
    out = pf.preflight("me@login.vbc.ac.at")
    assert out["case"] == "hostkey"
    assert out["hostkey"]["fingerprint"] == "SHA256:abc"


def test_preflight_rejects_invalid_before_any_ssh(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ssh must not run for invalid targets")
    monkeypatch.setattr(pf, "_ssh", boom)
    assert pf.preflight("host; rm -rf /")["case"] == "invalid"


def test_remote_facts_parses_canaries_and_accounts(monkeypatch):
    monkeypatch.setattr(pf, "_ssh", lambda *a, **k: _cp(
        0, "P:/groups/lab\n---\nlab-alloc\nlab-alloc\n"))
    out = pf.remote_facts("me@login.vbc.ac.at",
                          canary_paths=["/groups/lab", "/nonexistent"])
    assert out == {"ok": True, "present": ["/groups/lab"],
                   "accounts": ["lab-alloc"]}


def test_remote_facts_unreachable_is_classified(monkeypatch):
    monkeypatch.setattr(pf, "_ssh",
                        lambda *a, **k: _cp(255, "", "Connection refused"))
    out = pf.remote_facts("me@login.vbc.ac.at")
    assert out["ok"] is False and out["case"] == "network"
