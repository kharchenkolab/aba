"""provision-weft-ui (misc/compute_settings.md §8) — the installer provisions
the advanced compute surface: a pinned weft-ui checkout, an EDITABLE package
install (the served SPA lives in the checkout's web/dist, not the wheel), and
the SPA build with the env's own node/npm. Non-fatal by design on every
command — ABA works without it (Settings → Compute hides "Advanced ↗").

Shape-guards the playbook steps + runs the CLONE command against a throwaway
local repo (network-free), mirroring test_provision_weft.py.
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


def test_install_has_provision_weft_ui_in_order():
    doc = yaml.safe_load((HELPER / "install.yml").read_text())
    ids = [s["id"] for s in doc["steps"]]
    assert "provision-weft-ui" in ids
    # needs the env (pip/node) and comes after the substrate it fronts
    assert ids.index("provision-weft-ui") > ids.index("create-env")
    assert ids.index("provision-weft-ui") > ids.index("provision-weft")
    assert ids.index("provision-weft-ui") < ids.index("start-backend")


def test_provision_weft_ui_step_shape():
    cmds = _cmds(_steps("install.yml")["provision-weft-ui"])
    # the private-repo clone pattern (ref pin + URL/SRC overrides)
    for var in ("WEFT_UI_REF:-main", "ABA_WEFT_UI_URL", "ABA_WEFT_UI_SRC"):
        assert var in cmds, var
    # EDITABLE install (-e) — the wheel packages only server/weft_ui; the
    # served SPA resolves from the checkout — and --no-user (user-site would
    # shadow every env; see test_provision_weft.py for the full story)
    assert "-m pip install" in cmds and " -e " in cmds and "--no-user" in cmds
    assert "$REPO_DIR/weft-ui" in cmds
    # the SPA build uses the env's own node/npm (nodejs=20 ships in the env)
    assert "$ENV_DIR/bin/npm" in cmds and "vite build" in cmds
    # a source tree that already carries web/dist skips the build
    assert "dist/index.html" in cmds
    # NON-FATAL: every command ends in `true` — weft-ui is optional
    for c in _steps("install.yml")["provision-weft-ui"]["commands"]:
        assert c.rstrip().endswith("true"), c


def test_update_has_refresh_weft_ui_before_bounce():
    doc = yaml.safe_load((HELPER / "update.yml").read_text())
    ids = [s["id"] for s in doc["steps"]]
    assert "refresh-weft-ui" in ids
    assert ids.index("refresh-weft-ui") > ids.index("refresh-weft")
    assert ids.index("refresh-weft-ui") < ids.index("bounce-backend")
    cmds = _cmds(_steps("update.yml")["refresh-weft-ui"])
    assert "WEFT_UI_REF:-main" in cmds
    assert " -e " in cmds and "--no-user" in cmds
    # rebuild only when the checkout moved (the .built-ref stamp)
    assert ".built-ref" in cmds
    for c in _steps("update.yml")["refresh-weft-ui"]["commands"]:
        assert c.rstrip().endswith("true"), c


# ── the clone command honors WEFT_UI_REF against a real (local) repo ──────────

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def weft_ui_src(tmp_path):
    s = tmp_path / "weft-ui-src"
    s.mkdir()
    _git("init", "-q", ".", cwd=s)
    _git("config", "user.email", "t@t", cwd=s)
    _git("config", "user.name", "t", cwd=s)
    (s / "pyproject.toml").write_text("[project]\nname='weft-ui'\n")
    _git("add", "-A", cwd=s)
    _git("commit", "-qm", "A", cwd=s)
    _git("branch", "-m", "main", cwd=s)
    _git("tag", "v1", cwd=s)
    (s / "pyproject.toml").write_text("[project]\nname='weft-ui'\n# B\n")
    _git("commit", "-qam", "B", cwd=s)
    return s


def _clone_cmd() -> str:
    cmds = _steps("install.yml")["provision-weft-ui"]["commands"]
    return next(c for c in cmds if "REPO_DIR/weft-ui" in c and "git clone" in c)


def test_weft_ui_clone_pins_ref(weft_ui_src, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"REPO_DIR": str(repo), "WEFT_UI_REF": "v1",
           "ABA_WEFT_UI_URL": f"file://{weft_ui_src}", "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["sh", "-c", _clone_cmd()], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    body = (repo / "weft-ui" / "pyproject.toml").read_text()
    assert "# B" not in body            # pinned to v1, not main


def test_weft_ui_clone_src_override(weft_ui_src, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"REPO_DIR": str(repo), "ABA_WEFT_UI_SRC": str(weft_ui_src),
           "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["sh", "-c", _clone_cmd()], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (repo / "weft-ui" / "pyproject.toml").exists()
    assert not (repo / "weft-ui" / ".git").exists()   # rsync copy, not a clone


def test_weft_ui_clone_failure_is_nonfatal(tmp_path):
    """An unreachable repo prints the NOTE and exits 0 — install continues."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"REPO_DIR": str(repo), "WEFT_UI_REF": "main",
           "ABA_WEFT_UI_URL": f"file://{tmp_path}/nonexistent",
           "PATH": "/usr/bin:/bin"}
    r = subprocess.run(["sh", "-c", _clone_cmd()], env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "NOTE: weft-ui checkout failed" in r.stdout + r.stderr
