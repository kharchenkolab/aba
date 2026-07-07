"""ABA_REF / RECIPES_REF pinning — install clones + update pulls (and the CLI
bootstrap) must honor a branch / tag / commit pin, defaulting to `main`.

Guards the actual playbook command strings + the bootstrap against regression by
running them against a throwaway local git repo (main advanced past a `v1` tag)."""
import subprocess
from pathlib import Path

import pytest
import yaml


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def srcrepo(tmp_path):
    """A source repo: commit A (tagged v1) then commit B on main."""
    s = tmp_path / "src"
    s.mkdir()
    _git("init", "-q", ".", cwd=s)
    _git("config", "user.email", "t@t", cwd=s); _git("config", "user.name", "t", cwd=s)
    (s / "f").write_text("A"); _git("add", "-A", cwd=s); _git("commit", "-qm", "A", cwd=s)
    _git("branch", "-m", "main", cwd=s)   # default branch -> main (RHEL7 git 1.8.3.1 has no `init -b`)
    sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=s, capture_output=True, text=True).stdout.strip()
    _git("tag", "v1", cwd=s)
    (s / "f").write_text("B"); _git("commit", "-qam", "B", cwd=s)
    return {"path": s, "A": sha_a}


def _pkg_cmd(playbook: str, step_id: str, idx: int) -> str:
    import aba_installer
    pb = yaml.safe_load((Path(aba_installer.__file__).parent / playbook).read_text())
    return next(s for s in pb["steps"] if s["id"] == step_id)["commands"][idx]


def _head_subject(repo: Path) -> str:
    return subprocess.run(["git", "log", "-1", "--format=%s"], cwd=str(repo),
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("ref,expect", [("", "B"), ("v1", "A"), ("SHA", "A")])
def test_clone_honors_aba_ref(srcrepo, tmp_path, monkeypatch, ref, expect):
    cmd = _pkg_cmd("install.yml", "clone-repos", 1)   # the aba clone command
    repo_dir = tmp_path / "rd"
    ref_val = srcrepo["A"] if ref == "SHA" else ref
    env = {"REPO_DIR": str(repo_dir), "ABA_REPO_URL": str(srcrepo["path"]),
           "ABA_REF": ref_val, "PATH": __import__("os").environ["PATH"]}
    subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True)
    assert _head_subject(repo_dir / "aba") == expect


@pytest.mark.parametrize("ref,expect", [("", "B"), ("v1", "A"), ("SHA", "A")])
def test_pull_honors_aba_ref(srcrepo, tmp_path, ref, expect):
    cmd = _pkg_cmd("update.yml", "pull-aba", 0)
    repo_dir = tmp_path / "rd"; (repo_dir / "aba").parent.mkdir(parents=True, exist_ok=True)
    _git("clone", "-q", str(srcrepo["path"]), str(repo_dir / "aba"), cwd=tmp_path)  # start on main
    ref_val = srcrepo["A"] if ref == "SHA" else ref
    env = {"REPO_DIR": str(repo_dir), "ABA_REF": ref_val, "PATH": __import__("os").environ["PATH"]}
    subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True)
    assert _head_subject(repo_dir / "aba") == expect


def test_cli_bootstrap_honors_aba_ref(srcrepo, tmp_path, monkeypatch):
    """cli._bootstrap_repo_for_update pulls the repo to $ABA_REF before the update
    playbook loads (so a pin — or a newly added step at that ref — takes effect)."""
    from aba_installer import cli
    repo = tmp_path / "repo" / "aba"; repo.parent.mkdir(parents=True)
    _git("clone", "-q", str(srcrepo["path"]), str(repo), cwd=tmp_path)  # on main (B)
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("ABA_REF", "v1")
    monkeypatch.delenv("ABA_REPO_SRC", raising=False)
    cli._bootstrap_repo_for_update()
    assert _head_subject(repo) == "A"     # retargeted main → v1


# ── post-public acquisition contract: default = git checkout (one-command update);
#    ABA_REPO_SRC = file-copy override (dev/offline). See install/linux/setup.sh. ──

def test_clone_default_is_git_checkout(srcrepo, tmp_path):
    """No ABA_REPO_SRC → clone-repos git-clones a real .git checkout, so the deployed
    repo can `aba update` via git-pull. This is the post-public default that replaced
    the private-era file-copy (the whole point of dropping setup.sh's ABA_REPO_SRC=self)."""
    import os
    cmd = _pkg_cmd("install.yml", "clone-repos", 1)   # the aba clone command
    repo_dir = tmp_path / "rd"
    env = {"REPO_DIR": str(repo_dir), "ABA_REPO_URL": str(srcrepo["path"]),
           "PATH": os.environ["PATH"]}                # ABA_REPO_SRC intentionally UNSET
    subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True)
    assert (repo_dir / "aba" / ".git").is_dir(), "default install must be a git checkout"
    assert _head_subject(repo_dir / "aba") == "B"     # tracks main


def test_clone_with_src_is_filecopy(srcrepo, tmp_path):
    """ABA_REPO_SRC (dev/offline override) → rsync file-copy, NOT a checkout — such
    installs can't git-pull, so the CLI bootstrap must rsync-before-load to keep them
    current. Documents the two-mode contract the update path branches on."""
    import os
    cmd = _pkg_cmd("install.yml", "clone-repos", 1)
    repo_dir = tmp_path / "rd"
    env = {"REPO_DIR": str(repo_dir), "ABA_REPO_SRC": str(srcrepo["path"]),
           "PATH": os.environ["PATH"]}
    subprocess.run(["bash", "-c", cmd], env=env, check=True, capture_output=True)
    assert (repo_dir / "aba").is_dir() and not (repo_dir / "aba" / ".git").exists()


def test_setup_sh_does_not_force_repo_src():
    """Regression guard for the one-command-update simplification: setup.sh must NOT
    default ABA_REPO_SRC to the checkout (the private-era file-copy force) — post-public
    the default is a git clone. Re-adding the force silently reverts cluster/linux-personal
    to the file-copy path (stale-playbook-at-load, manual pre-sync)."""
    setup = Path(__file__).resolve().parents[3] / "linux" / "setup.sh"
    if not setup.is_file():
        pytest.skip("setup.sh not found relative to the test tree")
    text = setup.read_text()
    assert "ABA_REPO_SRC:-$REPO_ROOT" not in text, \
        "setup.sh re-introduced the ABA_REPO_SRC=self file-copy force"


if __name__ == "__main__":   # standalone runner (base env lacks pytest)
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
