"""Background model validation for Settings → Agent. A cheap call that confirms the
CURRENT credential can actually run a chosen model — so the UI shows ✓ Ready / ✗ reason
at select time instead of failing on the first real message. The high-value case is
OpenAI/Codex (an API-key model on a subscription, or a drifted Codex slug); Anthropic
models are `via: any` and the credential is verified on save, so we trust that."""
from __future__ import annotations

import os


def ping_model(model: str) -> dict:
    """{ok: bool, detail?: str}. Cheap; safe to call on model-select / panel-open."""
    if not model:
        return {"ok": False, "detail": "No model selected."}
    from core.llm_catalog import provider_for_model
    if provider_for_model(model) == "openai":
        return _ping_openai(model)
    # Anthropic: all catalog models are usable with a valid key OR OAuth, and the
    # credential was verified on save — a live 1-token call would need the oauth_cc
    # system marker to avoid a 429, so just trust the stored validity here.
    from core import credentials
    if not credentials.status("anthropic").get("valid"):
        return {"ok": False, "detail": "No valid Anthropic credential — connect one in Settings."}
    return {"ok": True}


def _ping_openai(model: str) -> dict:
    try:
        import openai
    except Exception:  # noqa: BLE001
        return {"ok": False, "detail": "The openai SDK isn't installed in this environment."}
    base = os.environ.get("ABA_OPENAI_BASE_URL") or "https://api.openai.com/v1"
    key = (os.environ.get("OPENAI_OAUTH_TOKEN") or os.environ.get("ABA_OPENAI_API_KEY")
           or os.environ.get("OPENAI_API_KEY") or "")
    if not key:
        return {"ok": False, "detail": "No OpenAI credential — connect one in Settings → Agent."}
    responses_mode = "/backend-api/codex" in base
    headers: dict = {}
    acct = os.environ.get("ABA_OPENAI_ACCOUNT_ID")
    if acct:
        headers["ChatGPT-Account-Id"] = acct
    if responses_mode:
        headers["originator"] = "codex_cli"
        headers["OpenAI-Beta"] = "responses=experimental"
    client = openai.OpenAI(base_url=base, api_key=key, default_headers=headers or None,
                           timeout=20.0, max_retries=0)
    try:
        if responses_mode:
            # Codex requires stream=True and rejects max_output_tokens; an unsupported
            # model 400s at create(). We break on the first stream event (response
            # accepted) so the ping costs ~no tokens.
            stream = client.responses.create(
                model=model, instructions="", store=False, stream=True,
                reasoning={"summary": "auto"},
                input=[{"role": "user", "content": [{"type": "input_text", "text": "ping"}]}])
            try:
                for ev in stream:
                    et = getattr(ev, "type", "")
                    if "error" in et:
                        return {"ok": False, "detail": f"Model check failed ({et})."}
                    if et.startswith("response."):
                        break
            finally:
                stream.close()
        else:
            client.chat.completions.create(
                model=model, max_tokens=1,
                messages=[{"role": "user", "content": "ping"}])
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        low = msg.lower()
        if "not supported when using codex" in low or "is not supported" in low:
            detail = (f"'{model}' isn't available on your ChatGPT/Codex subscription — pick a "
                      f"Codex model (gpt-5.x), or connect an OpenAI API key for gpt-4o/4.1.")
        elif "authentication" in low or "401" in low or "invalid" in low and "token" in low:
            detail = "Credential rejected — reconnect the provider in Settings → Agent."
        elif "model" in low and ("not found" in low or "does not exist" in low):
            detail = f"'{model}' isn't available to this credential."
        else:
            detail = f"Model check failed ({type(e).__name__})."
        return {"ok": False, "detail": detail}
