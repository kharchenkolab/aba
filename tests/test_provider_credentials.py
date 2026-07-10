"""Tier-1: multi-provider model catalog + credentials + runtime routing.
Anthropic path unchanged; OpenAI added (provider field, key path, runtime select)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def test_catalog_provider_and_filter():
    from core.llm_catalog import llm_models, provider_for_model, infer_provider
    rows = llm_models()
    assert all("provider" in r for r in rows)
    assert {r["provider"] for r in rows} <= {"anthropic", "openai"}
    assert all(r["provider"] == "anthropic" for r in llm_models("anthropic"))
    assert all(r["provider"] == "openai" for r in llm_models("openai"))
    assert provider_for_model("claude-opus-4-7") == "anthropic"
    assert provider_for_model("gpt-4o") == "openai"
    # inference fallback for an uncatalogued id
    assert infer_provider("o3-mini") == "openai"
    assert infer_provider("claude-x") == "anthropic"


def test_make_runtime_routes_by_provider():
    for k in ("ABA_RUNTIME_OVERRIDE", "ABA_FAKE_SESSION"):
        os.environ.pop(k, None)
    from core.runtime.agent import make_runtime, AgentSpec
    spec = AgentSpec(name="t", role="primary", model="claude-opus-4-7",
                     system_prompt="x", manifest_role="primary", runtime="direct")
    assert type(make_runtime(spec, model="gpt-4o")).__name__ == "OpenAICompatibleRuntime"
    assert "Direct" in type(make_runtime(spec, model="claude-opus-4-7")).__name__
    assert "Direct" in type(make_runtime(spec)).__name__            # no model → spec default
    # env override still wins
    os.environ["ABA_RUNTIME_OVERRIDE"] = "fake"
    assert type(make_runtime(spec, model="gpt-4o")).__name__ == "FakeRuntime"
    os.environ.pop("ABA_RUNTIME_OVERRIDE", None)


def test_openai_status_empty_and_key_format():
    from core import credentials
    # isolate config.env to a temp home so we don't read the real one
    import tempfile
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_cred_")
    for k in ("OPENAI_API_KEY", "ABA_OPENAI_API_KEY", "OPENAI_OAUTH_TOKEN"):
        os.environ.pop(k, None)
    st = credentials.status("openai")
    assert st["provider"] == "openai" and st["valid"] is False and st["has_api_key"] is False
    # bad key format is rejected BEFORE any network call
    try:
        credentials.set_credential("not-a-key", provider="openai")
        assert False, "should reject bad format"
    except ValueError as e:
        assert "OpenAI" in str(e)


def test_openai_set_key_persists(monkeypatch=None):
    from core import credentials
    import tempfile
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_cred2_")
    for k in ("OPENAI_API_KEY", "ABA_OPENAI_API_KEY", "ABA_OPENAI_BASE_URL"):
        os.environ.pop(k, None)
    # stub the network verify so the test is offline
    credentials._test_openai_credential = lambda key: (True, None)
    out = credentials.set_credential("sk-" + "a" * 40, provider="openai")
    assert out["provider"] == "openai" and out["has_api_key"] and out["valid"]
    # runtime-facing env is set + points at real OpenAI
    assert os.environ["ABA_OPENAI_API_KEY"].startswith("sk-")
    assert os.environ["ABA_OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    # persisted to config.env
    assert credentials.read().get("OPENAI_API_KEY", "").startswith("sk-")


def test_openai_runtime_real_vs_vllm_flag():
    """Pointed at api.openai.com the runtime must NOT send the vLLM-only
    chat_template_kwargs extension (real OpenAI 400s on unknown body fields)."""
    from core.runtime.llm_runtime_openai import OpenAICompatibleRuntime
    real = OpenAICompatibleRuntime(base_url="https://api.openai.com/v1", api_key="sk-x")
    vllm = OpenAICompatibleRuntime(base_url="http://localhost:8001/v1", api_key="none")
    assert real._real_openai is True and vllm._real_openai is False


def test_anthropic_status_unchanged_shape():
    from core import credentials
    st = credentials.status("anthropic")
    for k in ("provider", "mode", "has_api_key", "key_suffix", "has_oauth", "valid"):
        assert k in st
    assert st["provider"] == "anthropic"


if __name__ == "__main__":
    test_catalog_provider_and_filter(); print("ok  catalog provider + filter")
    test_make_runtime_routes_by_provider(); print("ok  runtime routes by provider")
    test_openai_status_empty_and_key_format(); print("ok  openai status + key format")
    test_openai_set_key_persists(); print("ok  openai key persist + runtime env")
    test_openai_runtime_real_vs_vllm_flag(); print("ok  real-openai vs vllm flag")
    test_anthropic_status_unchanged_shape(); print("ok  anthropic status shape")
    print("all provider-credential tests passed")
