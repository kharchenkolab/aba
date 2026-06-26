"""Per-project model selection + catalog-derived spec (Settings → LLM)."""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core import projects                                        # noqa: E402
from core.config import current_model_for_project                # noqa: E402
from core.llm_catalog import llm_models, spec_for_model, is_known_model  # noqa: E402
from core.runtime.agent import resolve_spec_for_turn             # noqa: E402


def test_catalog_has_anthropic_models_on_grounded_guide():
    models = llm_models()
    by = {m["model"]: m for m in models}
    assert "claude-haiku-4-5-20251001" in by
    assert "claude-sonnet-4-6" in by and "claude-opus-4-7" in by
    for m in models:
        assert m["spec"] == "grounded_guide"
    assert spec_for_model("claude-opus-4-7") == "grounded_guide"
    assert spec_for_model("some-unknown-model") is None
    assert is_known_model("claude-haiku-4-5-20251001") and not is_known_model("nope")


def test_per_project_model_storage_and_resolution():
    projects.init()
    for k in ("ABA_PRIMARY_MODEL", "ABA_MODEL"):
        os.environ.pop(k, None)
    pid = projects.create_project("model-test")["id"]
    projects.set_current(pid)
    assert projects.project_model(pid) == ""                 # nothing pinned yet
    # pin a model on the project
    projects.set_project_model(pid, "claude-opus-4-7")
    assert projects.project_model(pid) == "claude-opus-4-7"
    # resolution: project model wins (no env override)
    assert current_model_for_project(pid) == "claude-opus-4-7"
    # the SPEC follows the model via the catalog
    assert resolve_spec_for_turn(
        project_default=spec_for_model(current_model_for_project(pid))) == "grounded_guide"
    # ABA_MODEL is the deployment DEFAULT — the per-project choice overrides it
    os.environ["ABA_MODEL"] = "claude-sonnet-4-6"
    assert current_model_for_project(pid) == "claude-opus-4-7"   # project still wins
    os.environ.pop("ABA_MODEL")
    # ABA_PRIMARY_MODEL is the TARGETED operator override — it does beat the project
    os.environ["ABA_PRIMARY_MODEL"] = "claude-sonnet-4-6"
    assert current_model_for_project(pid) == "claude-sonnet-4-6"
    os.environ.pop("ABA_PRIMARY_MODEL")
    # a per-thread/request spec override still wins over the model's catalog spec
    assert resolve_spec_for_turn(thread_spec="lean_guide",
                                 project_default="grounded_guide") == "lean_guide"
    # clearing the project model falls through to the global/bundle default
    projects.set_project_model(pid, "")
    assert projects.project_model(pid) == ""
    assert current_model_for_project(pid)  # non-empty (bundle default_model)


def test_settings_llm_api():
    import os
    for k in ("ABA_PRIMARY_MODEL", "ABA_MODEL"):
        os.environ.pop(k, None)
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    projects.init()
    pid = projects.create_project("api-test")["id"]
    h = {"X-Project-Id": pid}
    r = client.get("/api/settings/llm", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert any(o["model"] == "claude-opus-4-7" for o in d["options"])
    assert d["current"]["pinned"] is False
    # pin opus
    r = client.post("/api/settings/llm", json={"model": "claude-opus-4-7"}, headers=h)
    assert r.status_code == 200, r.text
    cur = r.json()["current"]
    assert cur["model"] == "claude-opus-4-7" and cur["spec"] == "grounded_guide"
    # GET reflects the pin
    assert client.get("/api/settings/llm", headers=h).json()["current"]["pinned"] is True
    # unknown model rejected
    assert client.post("/api/settings/llm", json={"model": "bogus"}, headers=h).status_code == 400
    # clearing reverts
    assert client.post("/api/settings/llm", json={"model": ""}, headers=h).json()["current"]["pinned"] is False
