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
    _git("init", "-q", "-b", "main", ".", cwd=s)
    _git("config", "user.email", "t@t", cwd=s); _git("config", "user.name", "t", cwd=s)
    (s / "f").write_text("A"); _git("add", "-A", cwd=s); _git("commit", "-qm", "A", cwd=s)
    sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=s, capture_output=True, text=True).stdout.strip()
    _git("tag", "v1", cwd=s)
    (s / "f").write_text("B"); _git("commit", "-qam", "B", cwd=s)
    return {"path": s, "A": sha_a}


def _pkg_cmd(playbook: str, step_id: str, idx: int) -> str:
    import aba_installer
    pb = yaml.safe_load((Path(aba_installer.__file__).parent / playbook).read_text())
    return next(s for s in pb["steps"] if s["id"] == step_id)["commands"][idx]


def _head_subject(repo: Path) -> str:
    return subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%s"],
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
