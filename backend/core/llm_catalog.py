"""The install-wide model→spec catalog for the in-app model selector.

A user picks a MODEL per project (Settings → LLM); the agent spec it runs on is
derived from this table, not chosen separately. Source of truth is the layered
bundle's `llm_models` setting (system_bundle/settings.yaml, extendable by
institution/lab/user scopes); a hardcoded fallback keeps the picker working on a
bare bundle. Each entry: {label, model, spec}.
"""
from __future__ import annotations

# Mirrors backend/system_bundle/settings.yaml — the fallback when the bundle is
# unavailable. Keep the model ids in sync with the gateway's accepted models.
_FALLBACK: list[dict] = [
    {"label": "Haiku", "model": "claude-haiku-4-5-20251001", "spec": "grounded_guide"},
    {"label": "Sonnet", "model": "claude-sonnet-4-6", "spec": "grounded_guide"},
    {"label": "Opus", "model": "claude-opus-4-8", "spec": "grounded_guide"},
]


def llm_models() -> list[dict]:
    """The catalog: a list of {label, model, spec}. Bundle first, else fallback.
    Only well-formed entries (a non-empty `model`) are returned."""
    rows: list[dict] = []
    try:
        from core.bundle.active import get_bundle
        v = get_bundle().settings.get("llm_models")
        if isinstance(v, list):
            for m in v:
                if isinstance(m, dict) and (m.get("model") or "").strip():
                    rows.append({"label": m.get("label") or m["model"],
                                 "model": m["model"].strip(),
                                 "spec": (m.get("spec") or "").strip() or None})
    except Exception:  # noqa: BLE001 — never let a bundle issue break a turn
        rows = []
    return rows or [dict(r) for r in _FALLBACK]


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
