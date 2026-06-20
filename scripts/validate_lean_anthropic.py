"""End-to-end sanity check for the lean primary spec on Anthropic.

Loads the dumped lean fixtures (system prompt + Anthropic-shape tools
list) and drives ONE turn through anthropic.AsyncAnthropic with a
representative user message. Validates:

  - The lean system + tools list is accepted by the API (no 4xx).
  - The model picks a tool from the lean allowlist (not e.g. asking
    for a tool that was cut).
  - Prompt-token count tracks the dumper's projection (~9.6k static
    + small per-turn delta).
  - finish_reason is "tool_use" (not "max_tokens" or "stop_sequence").

Run from the aba repo root:
    .venv/bin/python scripts/validate_lean_anthropic.py

Uses the existing OAuth (~/.aba/oauth.json + CLAUDE_CODE_OAUTH_TOKEN)
through the project's `core.llm` credential resolver — no extra API
key needed if you're already logged in.

Exit codes: 0 ALL_PASS, 1 ASSERTION_FAIL, 2 NETWORK_OR_AUTH.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

FIXTURES = ROOT.parent / "local-llm" / "phase0" / "fixtures" / "lean_guide"

# Load lean fixtures dumped by scripts/dump_phase0_fixture.py.
system_text = (FIXTURES / "aba_system.txt").read_text()
tools_anthropic = json.loads((FIXTURES / "aba_tools_anthropic.json").read_text())

print(f"loaded {len(tools_anthropic)} lean tools, "
      f"{len(system_text):,} chars of system text "
      f"(~{len(system_text)//4:,} tokens)")
expected_tools = {t["name"] for t in tools_anthropic}


def _source_config_env() -> None:
    """Source ~/.aba/config.env so OAuth + ABA_* env vars are populated
    when the script is launched from a vanilla shell."""
    env_file = Path.home() / ".aba" / "config.env"
    if not env_file.is_file():
        return
    for ln in env_file.read_text().splitlines():
        ln = ln.strip()
        if not ln.startswith("export "):
            continue
        kv = ln[len("export "):].split("=", 1)
        if len(kv) == 2:
            os.environ.setdefault(kv[0], kv[1])


async def main() -> int:
    _source_config_env()
    # Use aba's own credential resolver — handles apikey / oauth /
    # oauth_cc and the auth_token-vs-api_key SDK distinction. Also
    # auto-refreshes expired OAuth tokens.
    os.environ.setdefault("ABA_DB_PATH",
                          str(Path(os.environ.get("ABA_RUNTIME_DIR",
                                                    "/tmp")) / "validate.db"))
    try:
        from core.llm import _llm_client, _wants_cc_marker, _CC_MARKER_BLOCK
        from core.llm import OAuthTokenUnavailable
    except Exception as e:                                       # noqa: BLE001
        print(f"NETWORK_OR_AUTH: core.llm import failed: {e}")
        return 2
    try:
        client = _llm_client()
    except OAuthTokenUnavailable as e:
        print(f"NETWORK_OR_AUTH: {e}")
        return 2

    # The cache_control flag on the LAST system block is what core.llm
    # uses; matching it keeps the call shape identical to a real Guide
    # turn. The CC marker (when oauth_cc) sits AHEAD of our text so
    # billing routes through the subscription.
    user_system_block = {"type": "text", "text": system_text,
                         "cache_control": {"type": "ephemeral"}}
    if _wants_cc_marker():
        system_blocks = [_CC_MARKER_BLOCK, user_system_block]
    else:
        system_blocks = [user_system_block]

    # Stick with Haiku regardless of ABA_MODEL — keeps cost low and
    # the lean spec defaults to it anyway.
    model = "claude-haiku-4-5-20251001"
    user_msg = "List the data files in this project."

    import anthropic
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=512,
            system=system_blocks,
            tools=tools_anthropic,
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.APIStatusError as e:
        print(f"NETWORK_OR_AUTH: API status {e.status_code}: {e.message}")
        return 2
    except anthropic.APIConnectionError as e:
        print(f"NETWORK_OR_AUTH: connection error: {e}")
        return 2

    # Inspect the response.
    print()
    print(f"model         : {model}")
    print(f"stop_reason   : {resp.stop_reason}")
    print(f"input_tokens  : {resp.usage.input_tokens:,}")
    print(f"output_tokens : {resp.usage.output_tokens:,}")
    cache_read  = getattr(resp.usage, "cache_read_input_tokens", None)
    cache_write = getattr(resp.usage, "cache_creation_input_tokens", None)
    if cache_read is not None or cache_write is not None:
        print(f"cache_read    : {cache_read}")
        print(f"cache_write   : {cache_write}")

    tool_uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
    text_blocks = [b for b in resp.content if getattr(b, "type", "") == "text"]
    print(f"tool_use blks : {len(tool_uses)}")
    for tu in tool_uses:
        print(f"  → {tu.name}  args={json.dumps(tu.input)[:200]}")
    if text_blocks:
        excerpt = "\n".join(b.text for b in text_blocks)[:300]
        print(f"text excerpt  : {excerpt!r}")

    # ── assertions ──────────────────────────────────────────────────
    failures: list[str] = []

    if resp.stop_reason != "tool_use":
        failures.append(
            f"stop_reason={resp.stop_reason!r}, expected 'tool_use' (the "
            "model should call a tool, not stop or hit max_tokens)")
    if not tool_uses:
        failures.append("model emitted no tool_use blocks at all")
    else:
        for tu in tool_uses:
            if tu.name not in expected_tools:
                failures.append(
                    f"model called tool {tu.name!r} which is NOT in the "
                    f"lean allowlist (would have errored at dispatch)")
    # Cache-control on our system block sends most tokens to
    # cache_write on the first call (or cache_read on subsequent ones).
    # Actual prompt size = input + cache_read + cache_write.
    total_prompt = (resp.usage.input_tokens
                    + (cache_read or 0) + (cache_write or 0))
    print(f"total prompt  : {total_prompt:,} tokens")
    if total_prompt > 14_000:
        failures.append(
            f"total prompt={total_prompt:,} exceeds 14k — static budget "
            "projection is off")
    if total_prompt < 7_000:
        failures.append(
            f"total prompt={total_prompt:,} is suspiciously small — "
            "lean fixtures may have failed to load")

    print()
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        print(f"ASSERTION_FAIL ({len(failures)})")
        return 1
    print("ALL_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
