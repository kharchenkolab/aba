"""pagoda3 copilot proxy request-shaping (core/viewers/llm_proxy.py). Pure; must
mirror pagoda3/server/proxy.mjs so behavior is identical whichever proxy runs."""
from core.viewers.llm_proxy import build_messages_request, anthropic_headers, CC_MARKER


def _payload(**kw):
    base = {"system": "SYS", "messages": [{"role": "user", "content": "hi"}],
            "model": "claude-opus-4-8", "max_tokens": 1000}
    base.update(kw)
    return base


def test_oauth_prepends_cc_marker():
    out = build_messages_request(_payload(), "oauth")
    assert out["system"][0]["text"] == CC_MARKER["text"]
    assert out["system"][1]["text"] == "SYS"


def test_apikey_has_no_cc_marker():
    out = build_messages_request(_payload(), "apikey")
    assert all(b["text"] != CC_MARKER["text"] for b in out["system"])
    assert out["system"][0]["text"] == "SYS"


def test_last_system_block_is_cache_marked():
    out = build_messages_request(_payload(), "oauth")
    assert out["system"][-1]["cache_control"] == {"type": "ephemeral"}
    # earlier blocks (the CC marker) are NOT cache-marked
    assert "cache_control" not in out["system"][0]


def test_last_tool_cache_marked():
    out = build_messages_request(_payload(tools=[{"name": "a"}, {"name": "b"}]), "oauth")
    assert "cache_control" not in out["tools"][0]
    assert out["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_string_last_message_becomes_cached_block():
    out = build_messages_request(_payload(), "oauth")
    lm = out["messages"][-1]
    assert isinstance(lm["content"], list)
    assert lm["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert lm["content"][-1]["text"] == "hi"


def test_list_last_message_marks_final_content_block():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}]
    out = build_messages_request(_payload(messages=msgs), "oauth")
    content = out["messages"][-1]["content"]
    assert "cache_control" not in content[0]
    assert content[-1]["cache_control"] == {"type": "ephemeral"}


def test_defaults_and_passthrough():
    out = build_messages_request({"messages": []}, "oauth")
    assert out["model"] == "claude-opus-4-8" and out["max_tokens"] == 4096
    assert out["stream"] is True
    out2 = build_messages_request(_payload(thinking={"type": "enabled"}), "oauth")
    assert out2["thinking"] == {"type": "enabled"}


def test_headers_oauth_vs_apikey():
    ho = anthropic_headers("oauth", "tok")
    assert ho["authorization"] == "Bearer tok"
    assert ho["anthropic-beta"] == "oauth-2025-04-20"
    assert "x-api-key" not in ho
    hk = anthropic_headers("apikey", "sk-ant-xxx")
    assert hk["x-api-key"] == "sk-ant-xxx"
    assert "authorization" not in hk
    assert ho["anthropic-version"] == "2023-06-01"
