"""Tiny prompt-provider registry. Domain-neutral.

Platform modules that need to fetch a named system prompt (e.g.
`core/summarize/budget_summary.py` asking for "thread_summary") look it
up here; content registers providers at startup via `register(name, fn)`.

Why a registry: the platform should ask "give me the thread_summary
prompt" without knowing where on disk the text lives, what language it's
in, or whether bio uses a per-locale variant. The PROVIDER function does
the resolution; the registry just holds the (name → callable) map.

The mirror pattern of `core/manifest/registry.py`'s card builders and
`core/hooks/dispatcher.py`'s hook handlers — content registers, platform
dispatches.

Used by misc/modularity_audit.md Phase C.2.
"""
from __future__ import annotations

from typing import Callable, Optional

# name → zero-arg function returning the prompt text
_PROVIDERS: dict[str, Callable[[], str]] = {}


def register(name: str, provider: Callable[[], str]) -> None:
    """Register a prompt provider. Idempotent — re-registering replaces."""
    _PROVIDERS[name] = provider


def get(name: str) -> Optional[str]:
    """Fetch the named prompt, or None if no provider is registered.
    Caller decides on the empty-string fallback (sometimes the right
    behavior is "skip the LLM call when no prompt is available")."""
    fn = _PROVIDERS.get(name)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # noqa: BLE001 — prompts are best-effort
        return None


def names() -> list[str]:
    """The set of currently-registered prompt names (debug / introspection)."""
    return sorted(_PROVIDERS)
