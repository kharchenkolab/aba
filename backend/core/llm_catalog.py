"""The install-wide model→spec catalog for the in-app model selector.

A user picks a PROVIDER + MODEL per project (Settings → Agent); the agent spec it
runs on is derived from this table, not chosen separately, and the provider selects
the LLM runtime (anthropic → DirectAPIRuntime, openai → OpenAICompatibleRuntime).
Source of truth is the layered bundle's `llm_models` setting
(system_bundle/settings.yaml, extendable by institution/lab/user scopes); a
hardcoded fallback keeps the picker working on a bare bundle. Each entry:
{label, model, spec, provider}.
"""
from __future__ import annotations

# Known providers. The provider selects the LLM runtime + which credential is used.
PROVIDERS = ("anthropic", "openai")


def infer_provider(model: str) -> str:
    """Best-effort provider from a model id when a catalog row omits `provider`:
    claude-* → anthropic, gpt-*/o1/o3/o4 (OpenAI reasoning) → openai; default
    anthropic (the historical single-provider assumption)."""
    m = (model or "").lower()
    if m.startswith(("gpt-", "gpt", "o1", "o3", "o4", "chatgpt", "text-", "davinci")):
        return "openai"
    return "anthropic"


# Mirrors backend/system_bundle/settings.yaml — the fallback when the bundle is
# unavailable. Keep the model ids in sync with the gateway's accepted models.
_FALLBACK: list[dict] = [
    {"label": "Opus", "model": "claude-opus-4-7", "spec": "grounded_guide", "provider": "anthropic"},
    {"label": "Sonnet", "model": "claude-sonnet-5", "spec": "grounded_guide", "provider": "anthropic"},
    {"label": "Haiku", "model": "claude-haiku-4-5-20251001", "spec": "grounded_guide", "provider": "anthropic"},
]


def llm_models(provider: str | None = None) -> list[dict]:
    """The catalog: a list of {label, model, spec, provider}. Bundle first, else
    fallback. Only well-formed entries (a non-empty `model`) are returned. When
    `provider` is given, only that provider's models are returned."""
    rows: list[dict] = []
    try:
        from core.bundle.active import get_bundle
        v = get_bundle().settings.get("llm_models")
        if isinstance(v, list):
            for m in v:
                if isinstance(m, dict) and (m.get("model") or "").strip():
                    mid = m["model"].strip()
                    prov = (m.get("provider") or "").strip().lower() or infer_provider(mid)
                    rows.append({"label": m.get("label") or mid,
                                 "model": mid,
                                 "spec": (m.get("spec") or "").strip() or None,
                                 "provider": prov if prov in PROVIDERS else "anthropic"})
    except Exception:  # noqa: BLE001 — never let a bundle issue break a turn
        rows = []
    rows = rows or [dict(r) for r in _FALLBACK]
    if provider:
        rows = [r for r in rows if r.get("provider") == provider]
    return rows


def provider_for_model(model: str) -> str:
    """The provider a model runs on, per the catalog (else inferred from the id).
    Drives runtime selection in core.runtime.agent.make_runtime."""
    if model:
        for m in llm_models():
            if m["model"] == model:
                return m.get("provider") or infer_provider(model)
    return infer_provider(model)


def spec_for_model(model: str) -> str | None:
    """The spec a given model runs on, per the catalog. None if unknown (caller
    then falls back to the bundle/default spec)."""
    if not model:
        return None
    for m in llm_models():
        if m["model"] == model:
            return m.get("spec")
    return None


def label_for_model(model: str) -> str | None:
    for m in llm_models():
        if m["model"] == model:
            return m.get("label")
    return None


def is_known_model(model: str) -> bool:
    return any(m["model"] == model for m in llm_models())
