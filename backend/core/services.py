"""Content-provided services — the inversion seam for the cases where `core/`
needs a VALUE or computation that only a content pack (e.g. bio) knows how to
produce, *without* `core/` importing `content/`.

Content registers a callable at import time; core calls it by name with a safe
fallback:

    # bio, at import time
    register_service("language_sniffer", _detect_language)
    # core, at the call site
    lang = call_service("language_sniffer", code, default="python")

Which seam to use:
  • ``core/hooks``    — fire-and-forget side-effect events (void handlers).
  • ``core/prompts``  — named prompt TEXT providers (``() -> str``).
  • ``core/services`` — value-returning callables of ANY signature (this module).

A missing pack must never break core — `call_service` returns the default when no
provider is registered or the provider raises.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

_SERVICES: dict[str, Callable[..., Any]] = {}


def register_service(name: str, fn: Callable[..., Any]) -> None:
    """Register a content-provided callable. Idempotent — re-registering replaces."""
    _SERVICES[name] = fn


def get_service(name: str) -> Optional[Callable[..., Any]]:
    """The registered callable for `name`, or None (caller supplies the fallback)."""
    return _SERVICES.get(name)


def call_service(name: str, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    """Call the registered service, or return `default` if none is registered or it
    raises. Content services are best-effort: a missing or buggy pack must not
    break core."""
    fn = _SERVICES.get(name)
    if fn is None:
        return default
    try:
        return fn(*args, **kwargs)
    except Exception:  # noqa: BLE001
        log.debug("content service %s raised; using default", name, exc_info=True)
        return default


def service_names() -> list[str]:
    """Diagnostic: the registered service names."""
    return sorted(_SERVICES)
