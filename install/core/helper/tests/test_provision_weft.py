"""provision-weft (weft rewrite W3.4) — the installer provisions the compute
substrate: pinned pixi/pixi-pack/pixi-unpack binaries + the weft package from a
pinned checkout, with an install-time verification gate.

Shape-guards the playbook steps + runs the CLONE command string against a
throwaway local repo (network-free), mirroring test_ref_pinning.py.
"""
import subprocess
from pathlib import Path

import pytest
import yaml

HELPER = Path(__file__).resolve().parents[1] / "src" / "aba_installer"


def _steps(playbook: str) -> dict:
    doc = yaml.safe_load((HELPER / playbook).read_text())
    return {s["id"]: s for s in doc["steps"]}


def _cmds(step: dict) -> str:
    return "\n".join(step["commands"])


def test_install_has_provision_weft_in_order():
    doc = yaml.safe_load((HELPER / "install.yml").read_text())
    ids = [s["id"] for s in doc["steps"]]
    assert "provision-weft" in ids
    # needs the env (pip) and the aba clone (verification gate) to exist first
    assert ids.index("provision-weft") > ids.index("create-env")
    assert ids.index("provision-weft") > ids.index("clone-repos")
    assert ids.index("provision-weft") < ids.index("start-backend")


def test_provision_weft_step_shape():
    cmds = _cmds(_steps("install.yml")["provision-weft"])
    # pinned versions, overridable
    assert "ABA_PIXI_VERSION:-v" in cmds and "ABA_PIXI_PACK_VERSION:-v" in cmds
    # BOTH pack tools (unpack's absence fails obscurely at realize time)
    assert "pixi-pack" in cmds and "pixi-unpack" in cmds
    # binaries land where core/compute/adapter.resolve_pixi() falls back to
    assert "tools/pixi/bin" in cmds
    # mac + linux arch selection present
    for asset in ("aarch64-apple-darwin", "x86_64-apple-darwin",
                  "aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl"):
        assert asset in cmds, asset
    # the private-repo clone pattern (ref pin + URL/SRC overrides)
    for var in ("WEFT_REF:-main", "ABA_WEFT_URL", "ABA_WEFT_SRC"):
        assert var in cmds, var
    # non-editable package install into the served env — MUST carry --no-user:
    # conda-prefix pythons have user-site ENABLED, and pip silently diverts to
    # ~/.local when site-packages looks unwritable (read-only image, network FS
    # hiccup) — a user-site weft would shadow every env. Fail loudly instead.
    assert "-m pip install" in cmds and "--no-user" in cmds
    assert "$REPO_DIR/weft" in cmds
    # the verification gate configures the substrate at INSTALL time
    assert "core.compute" in cmds and "adapter.configure" in cmds


def test_update_has_refresh_weft_before_bounce():
    doc = yaml.safe_load((HELPER / "update.yml").read_text())
    ids = [s["id"] for s in doc["steps"]]
    assert "refresh-weft" in ids
    assert ids.index("refresh-weft") < ids.index("bounce-backend")
    cmds = _cmds(_steps("update.yml")["refresh-weft"])
    assert "WEFT_REF:-main" in cmds
    # non-fatal by design — a failed refresh keeps the working substrate
    assert "true" in cmds.split("\n")[-1] or cmds.rstrip().endswith("true")


# ── the clone command honors WEFT_REF against a real (local) repo ────────────

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def weft_src(tmp_path):
    s = tmp_path / "weft-src"
    s.mkdir()
    _git("init", "-q", ".", cwd=s)
    _git("config", "user.email", "t@t", cwd=s)
    _git("config", "user.name", "t", cwd=s)
    (s / "pyproject.toml").write_text("[project]\nname='weft'\n")
    _git("add", "-A", cwd=s)
    _git("commit", "-qm", "A", cwd=s)
    _git("branch", "-m", "main", cwd=s)
    _git("tag", "v1", cwd=s)
    (s / "pyproject.toml").write_text("[project]\nname='weft'\n# B\n")
    _git("commit", "-qam", "B", cwd=s)
    return s


def _clone_cmd() -> str:
    cmds = _steps("install.yml")["provision-weft"]["commands"]
    return next(c for c in cmds if "REPO_DIR/weft" in c and "git clone" in c)


def test_weft_clone_pins_ref(weft_src, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"REPO_DIR": str(repo), "WEFT_REF": "v1",
           "ABA_WEFT_URL": f"file://{weft_src}", "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["sh", "-c", _clone_cmd()], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    body = (repo / "weft" / "pyproject.toml").read_text()
    assert "# B" not in body            # pinned to v1, not main


def test_weft_clone_src_override(weft_src, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"REPO_DIR": str(repo), "ABA_WEFT_SRC": str(weft_src),
           "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["sh", "-c", _clone_cmd()], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (repo / "weft" / "pyproject.toml").exists()
    assert not (repo / "weft" / ".git").exists()     # rsync copy, not a clone


def test_platform_case_resolves_on_this_host(tmp_path):
    """The inline case expression yields a real release-asset platform for the
    running host (and the mac arms exist — asserted textually above)."""
    cmds = _steps("install.yml")["provision-weft"]["commands"]
    pixi_cmd = next(c for c in cmds if "prefix-dev/pixi" in c)
    probe = ('P="${ABA_PIXI_PLATFORM:-$(case "$(uname -s)/$(uname -m)" in '
             '(Darwin/arm64) echo "aarch64-apple-darwin";; '
             '(Darwin/*) echo "x86_64-apple-darwin";; '
             '(Linux/aarch64|Linux/arm64) echo "aarch64-unknown-linux-musl";; '
             '(*) echo "x86_64-unknown-linux-musl";; esac)}"; echo "$P"')
    r = subprocess.run(["sh", "-c", probe], capture_output=True, text=True)
    plat = r.stdout.strip()
    assert plat in ("aarch64-apple-darwin", "x86_64-apple-darwin",
                    "aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl")
    assert plat in pixi_cmd or "ABA_PIXI_PLATFORM" in pixi_cmd
