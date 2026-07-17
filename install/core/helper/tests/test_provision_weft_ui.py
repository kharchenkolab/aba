"""provision-weft-ui (misc/compute_settings.md §8) — the installer provisions
the advanced compute surface: a weft-ui checkout, an EDITABLE package install
(the served SPA lives in the checkout's web/dist, not the wheel), and the SPA
build with the env's own node/npm. Non-fatal by design on every command — ABA
works without it (Settings → Compute hides "Advanced ↗").

DELIBERATELY ZERO settings of its own: everything derives from the weft trio
by the sits-beside-weft convention — source = the ABA_WEFT_SRC sibling dir
named weft-ui; URL = ABA_WEFT_URL + "-ui" (same org); ref = WEFT_REF when
weft-ui also carries that ref (tag both repos together to pin both), else
main. These tests guard the derivations against real (local) repos,
network-free, mirroring test_provision_weft.py.
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
    # NO settings of its own — derives from the weft trio (the whole point:
    # every new env var is a support burden). A WEFT_UI-prefixed variable
    # reappearing here is a regression.
    assert "WEFT_UI_REF" not in cmds and "ABA_WEFT_UI_URL" not in cmds \
        and "ABA_WEFT_UI_SRC" not in cmds
    for derived in ("ABA_WEFT_URL", "ABA_WEFT_SRC", "WEFT_REF"):
        assert derived in cmds, derived
    assert '-ui"' in cmds or "-ui;" in cmds or "}-ui" in cmds  # URL derivation
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
    assert "WEFT_UI_REF" not in cmds and "ABA_WEFT_UI" not in cmds
    assert " -e " in cmds and "--no-user" in cmds
    # rebuild only when the checkout moved (the .built-ref stamp)
    assert ".built-ref" in cmds
    for c in _steps("update.yml")["refresh-weft-ui"]["commands"]:
        assert c.rstrip().endswith("true"), c


# ── the derivations, against real (local) repos ──────────────────────────────

def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _mkrepo(path: Path, name: str, tag: str | None = None) -> Path:
    path.mkdir(parents=True)
    _git("init", "-q", ".", cwd=path)
    _git("config", "user.email", "t@t", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    (path / "pyproject.toml").write_text(f"[project]\nname='{name}'\n")
    _git("add", "-A", cwd=path)
    _git("commit", "-qm", "A", cwd=path)
    _git("branch", "-m", "main", cwd=path)
    if tag:
        _git("tag", tag, cwd=path)
        (path / "pyproject.toml").write_text(f"[project]\nname='{name}'\n# B\n")
        _git("commit", "-qam", "B", cwd=path)
    return path


@pytest.fixture
def repos(tmp_path):
    """Sibling weft + weft-ui repos, both tagged v1 (the tag-both convention)."""
    weft = _mkrepo(tmp_path / "weft", "weft", tag="v1")
    weft_ui = _mkrepo(tmp_path / "weft-ui", "weft-ui", tag="v1")
    return weft, weft_ui


def _clone_cmd() -> str:
    cmds = _steps("install.yml")["provision-weft-ui"]["commands"]
    return next(c for c in cmds if "REPO_DIR/weft-ui" in c and "git clone" in c)


def _run(env_extra: dict, tmp_path) -> tuple[Path, subprocess.CompletedProcess]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    env = {"REPO_DIR": str(repo), "PATH": "/usr/bin:/bin", **env_extra}
    r = subprocess.run(["sh", "-c", _clone_cmd()], env=env,
                       capture_output=True, text=True)
    return repo, r


def test_url_derives_from_weft_url(repos, tmp_path):
    weft, _ = repos
    repo, r = _run({"ABA_WEFT_URL": f"file://{weft}"}, tmp_path)
    assert r.returncode == 0, r.stderr
    # cloned the SIBLING repo (…/weft → …/weft-ui), default ref main
    body = (repo / "weft-ui" / "pyproject.toml").read_text()
    assert "name='weft-ui'" in body and "# B" in body   # main tip, not v1


def test_ref_shared_when_weft_ui_carries_it(repos, tmp_path):
    weft, _ = repos
    repo, r = _run({"ABA_WEFT_URL": f"file://{weft}", "WEFT_REF": "v1"}, tmp_path)
    assert r.returncode == 0, r.stderr
    body = (repo / "weft-ui" / "pyproject.toml").read_text()
    assert "# B" not in body                            # pinned to shared v1


def test_ref_falls_back_to_main_when_absent(repos, tmp_path):
    weft, weft_ui = repos
    _git("tag", "weft-only-tag", cwd=weft)              # not in weft-ui
    repo, r = _run({"ABA_WEFT_URL": f"file://{weft}",
                    "WEFT_REF": "weft-only-tag"}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert (repo / "weft-ui" / "pyproject.toml").exists()
    assert "@ main" in r.stdout                         # visible, not silent


def test_src_derives_from_weft_src_sibling(repos, tmp_path):
    weft, _ = repos
    repo, r = _run({"ABA_WEFT_SRC": str(weft)}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert (repo / "weft-ui" / "pyproject.toml").exists()
    assert not (repo / "weft-ui" / ".git").exists()     # rsync copy, not clone
    assert "sibling source" in r.stdout


def test_clone_failure_is_nonfatal(tmp_path):
    """An unreachable derived URL prints the NOTE and exits 0."""
    repo, r = _run({"ABA_WEFT_URL": f"file://{tmp_path}/nonexistent"}, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "NOTE: weft-ui checkout failed" in r.stdout + r.stderr
