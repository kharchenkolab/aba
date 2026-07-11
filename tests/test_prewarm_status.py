"""Lazy-env-init Phase C (backend): the env/module prewarm-status endpoint that
powers the ambient 'setting up…' pill + EnvironmentTab (lazy_env_init.md)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.exec.env_integrity as ei                                  # noqa: E402
from core.web.routers.settings import settings_environment_prewarm    # noqa: E402


def test_prewarm_status_shape():
    r = settings_environment_prewarm()
    assert set(r) >= {"prewarm", "stage", "setting_up", "modules"}
    assert {m["id"] for m in r["modules"]} == {"single_cell", "deep_learning", "r_bioc"}
    for m in r["modules"]:
        assert set(m) >= {"id", "label", "ready"} and isinstance(m["ready"], bool)


def test_prewarm_status_setting_up_flag(monkeypatch):
    monkeypatch.setattr(ei, "base_stage", lambda: "completing")
    r = settings_environment_prewarm()
    assert r["stage"] == "completing" and r["setting_up"] is True
    monkeypatch.setattr(ei, "base_stage", lambda: "ready")
    r = settings_environment_prewarm()
    assert r["stage"] == "ready" and r["setting_up"] is False
