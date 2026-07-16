"""SSH access & trust for Settings→Compute (misc/compute_settings.md §5.2).

The probes that run BEFORE a site exists: reachability preflight with a
classified cause, trust-on-first-use host-key capture, dedicated-key setup,
and a one-call remote-facts probe (shared-fs canary + billing accounts).

Ported in spirit from weft-ui's wizard.py with the same security posture —
never a shell (argv lists only), destinations/options validated against
tight patterns, `BatchMode=yes` always (nothing here can prompt) — plus two
aba-specific rules:

  * **aba never handles the user's password.** `keysetup()` generates a
    dedicated keypair and returns the exact `ssh-copy-id` line for the user
    to run in their OWN terminal; there is no password parameter anywhere
    in this module, by design.
  * **Host keys are TOFU with explicit consent.** Unknown keys fail the
    preflight with the real fingerprint attached; `accept_hostkey()` (called
    only after the user confirms in the UI) records it in aba's own
    known-hosts file under $ABA_HOME — the user's ~/.ssh/known_hosts is
    read, never written.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from core import config

DEST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.@-]*[A-Za-z0-9])?$")
SAFE_OPT_RE = re.compile(
    r"^(?:-i|-o|-p|-J|-[46])$|"                        # option flags passed through
    r"^[A-Za-z]+=[A-Za-z0-9_./@:, ~-]+$|"              # -o Key=Value bodies
    r"^[A-Za-z0-9_./@:~-]+$"                           # -i path / -J jump / -p port bodies
)
DENY_OPT_KEYS = re.compile(r"^(proxycommand|localcommand|permitlocalcommand|"
                           r"remotecommand|knownhostscommand)=", re.IGNORECASE)
SSH_BASE = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]

KEY_COMMENT = "aba"


def validate_target(dest: str, opts: list[str]) -> Optional[str]:
    """None when (dest, opts) are safe to put on an argv; else the reason."""
    if not DEST_RE.match(dest.split("@")[-1]) or dest.count("@") > 1:
        return f"destination {dest!r} is not a plain host/user@host"
    for o in opts:
        if not SAFE_OPT_RE.match(o) or DENY_OPT_KEYS.match(o):
            return f"ssh option {o!r} not allowed"
    return None


def known_hosts_path() -> Path:
    """aba's own TOFU store — consulted alongside (never instead of) the
    user's ~/.ssh/known_hosts, and the only one aba writes."""
    return config.aba_home() / "state" / "ssh_known_hosts"


def trust_opts() -> list[str]:
    """ssh options making host-key checks deterministic for BatchMode runs:
    both known-hosts files are consulted; an unknown key FAILS (classified
    'hostkey') instead of hanging on a prompt."""
    return ["-o",
            f"UserKnownHostsFile=~/.ssh/known_hosts {known_hosts_path()}",
            "-o", "StrictHostKeyChecking=yes"]


def _run(argv: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def identity_opts() -> list[str]:
    """`-i` for aba's dedicated key once it exists — ssh only tries default
    identity names, so the aba key must be offered explicitly."""
    key, _ = key_paths()
    return ["-i", str(key)] if key.exists() else []


def _ssh(dest: str, port: Optional[int], opts: list[str], command: str,
         timeout: int = 20) -> subprocess.CompletedProcess:
    argv = ["ssh", *SSH_BASE, *trust_opts(), *identity_opts(), *opts]
    if port:
        argv += ["-p", str(port)]
    argv += [dest, command]
    try:
        return _run(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 255, "", "connection timed out")


# ---- classification (pure — unit-tested) -------------------------------------

def classify(returncode: int, stderr: str) -> str:
    """ssh stderr → ok | auth | hostkey | dns | network | unknown."""
    if returncode == 0:
        return "ok"
    s = stderr.lower()
    if "permission denied" in s or "no supported authentication" in s \
            or "too many authentication failures" in s:
        return "auth"
    if "host key verification failed" in s \
            or "remote host identification has changed" in s \
            or "no ed25519 host key is known" in s \
            or re.search(r"no \S+ host key is known", s):
        return "hostkey"
    if "could not resolve hostname" in s or "name or service not known" in s:
        return "dns"
    if "connection refused" in s or "timed out" in s or "no route to host" in s \
            or "network is unreachable" in s:
        return "network"
    return "unknown"


# the tab's causal one-liners (§5.2 / §6) — precise classifier, plain words
CAUSE = {
    "auth": "the machine answered, but won't accept aba without a password — "
            "set up key access below",
    "hostkey": "first time connecting — confirm the machine's identity below",
    "dns": "that name doesn't resolve from here — check the spelling, or it "
           "may only exist on the lab network (VPN)",
    "network": "couldn't reach it from here — if this machine usually needs "
               "the VPN, connect it and try again",
    "unknown": "something else went wrong — the full message is below",
}


# ---- ~/.ssh/config discovery (pure — unit-tested) -----------------------------

def parse_ssh_config(text: str) -> list[dict]:
    """Concrete Host blocks (wildcards skipped) → the saved-hosts picker.
    Reads only Host/HostName/User/Port/ProxyJump."""
    hosts: list[dict] = []
    current: list[dict] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, _, value = line.partition(" ")
        key, value = key.lower(), value.strip()
        if key == "host":
            current = [{"host": h} for h in value.split()
                       if not any(c in h for c in "*?!")]
            hosts.extend(current)
        elif current and key in ("hostname", "user", "port", "proxyjump"):
            for h in current:
                h[{"hostname": "hostname", "user": "user",
                   "port": "port", "proxyjump": "jump"}[key]] = value
    return hosts


def ssh_config_hosts() -> list[dict]:
    path = Path.home() / ".ssh" / "config"
    if not path.exists():
        return []
    return parse_ssh_config(path.read_text())


# ---- host-key TOFU -------------------------------------------------------------

def scan_hostkey(dest: str, port: Optional[int] = None) -> Optional[dict]:
    """The machine's current host key: {'fingerprint','keytype','line'} or
    None when unreachable. keyscan output is held for accept_hostkey — it is
    NOT trusted until the user confirms the fingerprint."""
    host = dest.rsplit("@", 1)[-1]
    argv = ["ssh-keyscan", "-T", "8", "-t", "ed25519,ecdsa,rsa"]
    if port:
        argv += ["-p", str(port)]
    argv.append(host)
    r = _run(argv, timeout=15)
    lines = [l for l in r.stdout.splitlines()
             if l.strip() and not l.startswith("#")]
    if not lines:
        return None
    line = sorted(lines, key=lambda l: "ed25519" not in l)[0]  # prefer ed25519
    return {"line": line, **_fingerprint_of(line)}


def _fingerprint_of(keyline: str) -> dict:
    import base64
    import hashlib
    try:
        _, keytype, b64 = keyline.split()[:3]
        digest = hashlib.sha256(base64.b64decode(b64)).digest()
        fp = base64.b64encode(digest).decode().rstrip("=")
        return {"fingerprint": f"SHA256:{fp}", "keytype": keytype}
    except Exception:  # noqa: BLE001 — malformed keyscan line
        return {"fingerprint": "", "keytype": ""}


def accept_hostkey(keyline: str) -> Path:
    """Record a user-confirmed host key in aba's known-hosts store
    (append-once). Returns the store path."""
    path = known_hosts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""
    if keyline.strip() and keyline.strip() not in existing:
        with path.open("a") as f:
            f.write(keyline.strip() + "\n")
    return path


# ---- dedicated key setup (no-password contract) --------------------------------

def key_paths() -> tuple[Path, Path]:
    ssh_dir = Path.home() / ".ssh"
    return ssh_dir / "aba_ed25519", ssh_dir / "aba_ed25519.pub"


def keysetup(dest: str, port: Optional[int] = None) -> dict:
    """Ensure aba's dedicated keypair exists and hand back the one command
    the user runs in their own terminal. Idempotent; generation is local
    (`ssh-keygen -N ''`), installation is the user's ssh, not ours."""
    key, pub = key_paths()
    created = False
    if not key.exists():
        key.parent.mkdir(mode=0o700, exist_ok=True)
        r = _run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", KEY_COMMENT,
                  "-f", str(key)], timeout=20)
        if r.returncode != 0:
            return {"ok": False, "detail": r.stderr[-500:]}
        created = True
    target = dest if not port else f"-p {port} {dest}"
    return {"ok": True, "created": created,
            "key_path": str(key), "pub_path": str(pub),
            "command": f"ssh-copy-id -i {pub} {target}",
            "identity_opts": ["-i", str(key)]}


# ---- the probes ---------------------------------------------------------------

def preflight(dest: str, port: Optional[int] = None,
              ssh_opts: Optional[list[str]] = None) -> dict:
    """Reachability + cause. On 'hostkey' the response carries the machine's
    actual fingerprint for the §5.2 confirm card."""
    opts = list(ssh_opts or [])
    if err := validate_target(dest, opts):
        return {"case": "invalid", "detail": err}
    r = _ssh(dest, port, opts, "true")
    case = classify(r.returncode, r.stderr)
    out = {"case": case, "cause": CAUSE.get(case, ""),
           "stderr": (r.stderr or "")[-2000:]}
    if case == "hostkey":
        hk = scan_hostkey(dest, port)
        if hk:
            out["hostkey"] = hk
    return out


def remote_facts(dest: str, port: Optional[int] = None,
                 ssh_opts: Optional[list[str]] = None,
                 canary_paths: Optional[list[str]] = None) -> dict:
    """One ssh round-trip for the facts weft's probe can't know are ours:
    which DEPLOYMENT paths exist there (the shared-fs canary, §11 #3) and
    the user's scheduler billing accounts. rc reflects connectivity only."""
    opts = list(ssh_opts or [])
    if err := validate_target(dest, opts):
        return {"ok": False, "detail": err}
    paths = [p for p in (canary_paths or []) if re.fullmatch(r"[\w./@ -]+", p)]
    checks = "; ".join(f"test -e '{p}' && echo 'P:{p}'" for p in paths)
    cmd = ((checks + "; ") if checks else "") + \
        "command -v sbatch >/dev/null 2>&1 && echo 'SCHED:slurm'; " \
        "echo '---'; sacctmgr -nP show assoc user=$USER format=account " \
        "2>/dev/null | sort -u; true"
    r = _ssh(dest, port, opts, cmd, timeout=30)
    if r.returncode != 0:
        return {"ok": False, "case": classify(r.returncode, r.stderr),
                "detail": (r.stderr or "")[-500:]}
    head, _, tail = r.stdout.partition("---")
    present = [l[2:].strip() for l in head.splitlines() if l.startswith("P:")]
    scheduler = next((l[6:].strip() for l in head.splitlines()
                      if l.startswith("SCHED:")), "none")
    accounts = sorted({a.split("|")[0].strip()
                       for a in tail.splitlines() if a.strip()})
    return {"ok": True, "present": present, "scheduler": scheduler,
            "accounts": accounts}
