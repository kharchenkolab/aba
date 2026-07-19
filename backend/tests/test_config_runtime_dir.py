"""Guard: ABA_RUNTIME_DIR must default to a PER-USER path, never a shared
absolute one. Two personal installs that both fell back to one /workspace path
would collide on projects/artifacts (silent overwrite) or hard-fail on a
read-only mount — a bug hit on multiple real installs. Explicit injection
(container/OOD deploys) must still win. See config._resolve_runtime_dir."""
from pathlib import Path

from core import config


def test_runtime_dir_default_is_per_user_never_workspace(monkeypatch):
    monkeypatch.delenv("ABA_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("ABA_HOME", raising=False)
    p = config._resolve_runtime_dir()
    assert p == (Path.home() / ".aba" / "runtime").resolve()
    # never the shared absolute default — the collision/overwrite hazard
    assert not str(p).startswith("/workspace")


def test_runtime_dir_derives_from_aba_home(monkeypatch):
    monkeypatch.delenv("ABA_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("ABA_HOME", "/scratch/somebody/aba")
    assert config._resolve_runtime_dir() == Path("/scratch/somebody/aba/runtime").resolve()


def test_runtime_dir_explicit_injection_wins(monkeypatch):
    # container/OOD deploys inject ABA_RUNTIME_DIR explicitly (deploy_injected)
    monkeypatch.setenv("ABA_RUNTIME_DIR", "/groups/lab/aba/alice")
    assert config._resolve_runtime_dir() == Path("/groups/lab/aba/alice").resolve()
