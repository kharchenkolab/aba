"""Install-wide default model — Settings → Agent model selection works with NO project.

The Model pane is project-OPTIONAL: with a project open it pins per-project; with none
(fresh install) it reads/writes the install-wide default (ABA_MODEL in config.env), so
a user can pick a default and ping it without first creating a project.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.config as config                          # noqa: E402
from core.web import deps                             # noqa: E402
from core.web.routers import settings as st           # noqa: E402


def test_optional_project_none_when_no_context(monkeypatch):
    monkeypatch.setattr(deps._projects, "current", lambda: None)
    assert deps.optional_project(project_id=None, x_project_id=None) is None


def test_optional_project_pins_when_given(monkeypatch):
    seen = {}
    monkeypatch.setattr(deps._projects, "current", lambda: None)
    monkeypatch.setattr(deps._projects, "set_current", lambda p: seen.setdefault("cur", p))
    assert deps.optional_project(project_id="p1", x_project_id=None) == "p1"
    assert seen["cur"] == "p1"


def test_set_default_model_roundtrips_config_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.delenv("ABA_MODEL", raising=False)
    (tmp_path / "config.env").write_text("ABA_ENV_PREWARM=staged\nABA_REF=lazymac\n")

    config.set_default_model("claude-opus-4-7")
    cfg = (tmp_path / "config.env").read_text()
    assert "ABA_MODEL=claude-opus-4-7" in cfg
    assert "ABA_ENV_PREWARM=staged" in cfg and "ABA_REF=lazymac" in cfg   # others preserved
    assert config._read_aba_model_from_config_env() == "claude-opus-4-7"
    import os
    assert os.environ["ABA_MODEL"] == "claude-opus-4-7"                    # live in-process

    config.set_default_model("")                                          # clear
    assert "ABA_MODEL" not in (tmp_path / "config.env").read_text()
    assert "ABA_MODEL" not in os.environ


def test_llm_get_works_without_project(monkeypatch):
    """GET returns options + a current model even with no project (no 412)."""
    r = st.settings_llm_get(_pid=None)
    assert isinstance(r["options"], list) and r["options"]
    assert r["current"]["model"]                    # a resolved default, not empty
    assert r["current"]["pinned"] is False


def test_llm_set_no_project_writes_install_default(monkeypatch):
    calls = {}
    monkeypatch.setattr(st, "_llm_current", lambda pid: {"model": "claude-opus-4-7"})
    monkeypatch.setattr("core.llm_catalog.is_known_model", lambda m: True)
    monkeypatch.setattr("core.config.set_default_model", lambda m: calls.setdefault("global", m))
    monkeypatch.setattr("core.projects.set_project_model", lambda p, m: calls.setdefault("proj", (p, m)))

    st.settings_llm_set(st.LlmSelectRequest(model="claude-opus-4-7"), _pid=None)
    assert calls == {"global": "claude-opus-4-7"}    # install-wide, NOT a project pin


def test_llm_set_with_project_pins_project(monkeypatch):
    calls = {}
    monkeypatch.setattr(st, "_llm_current", lambda pid: {"model": "claude-opus-4-7"})
    monkeypatch.setattr("core.llm_catalog.is_known_model", lambda m: True)
    monkeypatch.setattr("core.config.set_default_model", lambda m: calls.setdefault("global", m))
    monkeypatch.setattr("core.projects.set_project_model", lambda p, m: calls.setdefault("proj", (p, m)))

    st.settings_llm_set(st.LlmSelectRequest(model="claude-opus-4-7"), _pid="p1")
    assert calls == {"proj": ("p1", "claude-opus-4-7")}
