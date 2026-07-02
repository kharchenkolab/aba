"""Let a co-hosted external viewer (pagoda3) borrow ABA's Anthropic credential
so its in-viewer copilot works with no key of its own — and, critically, WITHOUT
the viewer ever touching ABA's OAuth token. ABA is the proxy; all token renewal
stays in core.llm (_oauth_bearer, the single locked refresher). The viewer only
relays request/response.

This mirrors pagoda3's own server/proxy.mjs request contract so its browser
client speaks to ABA unchanged (POST {base}/agent/stream, GET {base}/health):
for OAuth it prepends the Claude-Code system marker and stamps prompt-cache
`cache_control` on the last system / tool / message block. Pure request-shaping
lives here (testable); the network relay + auth live in the FastAPI endpoint.
"""
from __future__ import annotations

from typing import Any

# Byte-exact first system block the Messages API requires for an OAuth bearer on
# non-Haiku models (same string as proxy.mjs / the Claude Agent SDK).
CC_MARKER = {"type": "text", "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK."}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def _cached(block: dict) -> dict:
    """Copy of a block with an ephemeral prompt-cache marker."""
    return {**block, "cache_control": {"type": "ephemeral"}}


def build_messages_request(payload: dict, mode: str) -> dict[str, Any]:
    """Transform pagoda3's `{system, messages, tools, model, max_tokens,
    thinking?}` into an Anthropic Messages body. For OAuth modes the CC marker
    leads the system array (unlocks non-Haiku models). Prompt-cache markers go
    on the LAST system block, LAST tool, and LAST message so a conversation's
    stable prefix is written once and read cheaply on later turns — matching
    proxy.mjs so behavior is identical whichever proxy runs."""
    sys_blocks: list[dict] = []
    if mode != "apikey":
        sys_blocks.append(dict(CC_MARKER))
    if payload.get("system"):
        sys_blocks.append({"type": "text", "text": str(payload["system"])})

    out: dict[str, Any] = {
        "model": payload.get("model") or "claude-opus-4-8",
        "max_tokens": payload.get("max_tokens") or 4096,
        "stream": True,
        "system": sys_blocks,
        "messages": list(payload.get("messages") or []),
    }
    if payload.get("tools"):
        out["tools"] = list(payload["tools"])

    if out["system"]:
        out["system"][-1] = _cached(out["system"][-1])
    if out.get("tools"):
        out["tools"][-1] = _cached(out["tools"][-1])

    msgs = out["messages"]
    if msgs:
        lm = dict(msgs[-1])
        content = lm.get("content")
        if isinstance(content, str):
            lm["content"] = [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
        elif isinstance(content, list) and content:
            lm["content"] = list(content[:-1]) + [_cached(content[-1])]
        msgs[-1] = lm

    if payload.get("thinking"):
        out["thinking"] = payload["thinking"]
    return out


def anthropic_headers(mode: str, token: str) -> dict[str, str]:
    """Upstream headers for api.anthropic.com. OAuth → Bearer + the oauth beta
    flag; apikey → x-api-key."""
    h = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
    if mode == "apikey":
        h["x-api-key"] = token
    else:
        h["authorization"] = f"Bearer {token}"
        h["anthropic-beta"] = "oauth-2025-04-20"
    return h
