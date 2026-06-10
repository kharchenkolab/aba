"""ContentPack — registration interface for a content vertical (bio, …).

Today the orchestrator (guide.py) directly imports content/bio/* for
prompts, tools, advisors, lifecycle hooks, and per-type card builders.
Audit `misc/modularity_audit.md` flagged this as the load-bearing leak
that keeps audit-criterion #7 ("hypothetical content/legal/ could slot
in alongside content/bio/") at PARTIAL.

A ContentPack is a single object the platform receives at startup. The
orchestrator queries it for prompts/tools/cards and the pack runs its
own side-effect registrations (hook handlers, advisor specs) at the
same moment. The orchestrator never imports a specific content
package — `active_pack()` returns whichever pack `main.py` registered.

Layering note: this protocol is intentionally narrow + matches the
TODAY shapes (TOOL_SCHEMAS list, execute_tool callable, prompt
builder callables). It is NOT designed for an imaginary future vertical
— that would be speculation. When a second vertical surfaces, the
protocol shape may need to adjust; the cost of doing so is bounded
because there's only one consumer (guide.py).

The protocol is consumed in only two places after Wave 1 Track A.3:
- guide.py — pre-call build_system() / TOOL_SCHEMAS / cards lookups
- backend/main.py startup — set_active_pack(pack) + pack.register_hooks()

Adding a new content pack (hypothetical):
1. Implement the protocol in your package's __init__.py (or a pack.py
   alongside it).
2. In main.py startup, swap the bio pack import for yours.
3. That's it. No core/ edits.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol


class ContentPack(Protocol):
    """A pack a content vertical exposes to the platform.

    Today, only `content.bio` implements this. The protocol's shape
    follows the actual call sites in guide.py, not a speculative second
    consumer's needs.
    """
    # Identity — for logs / observability. Free-form; bio uses "bio".
    name: str

    def prompts(self) -> dict[str, Callable[..., Any]]:
        """Named prompt builders. Today bio returns:
            {
              "system":              build_system,            # (thread_id, ...) -> SystemSpec-equivalent
              "recipes_reminder":    build_recipes_reminder,  # () -> str
              "focus_preamble":      render_focus_preamble,   # (entity) -> str
            }
        guide.py looks these up by name; missing keys yield empty.
        """
        ...

    def tools(self) -> list[dict]:
        """Tool schemas in Anthropic shape. Today: bio.tools.TOOL_SCHEMAS."""
        ...

    def execute_tool(self) -> Callable[..., Any]:
        """Per-tool dispatcher. Same signature as bio.tools.execute_tool:
        (tool_name: str, tool_input: dict, ctx: dict | None) -> dict
        (sync or async — orchestrator awaits if coroutine)."""
        ...

    def cards(self) -> dict[str, Callable[..., Any]]:
        """Per-entity-type card builders for the manifest assembler.
        Today bio returns whatever content.bio.cards' registration
        established. Keys are entity-type strings; values are
        (entity, context) -> str.
        """
        ...

    def register_hooks(self) -> None:
        """Side-effect: register on_post_tool / on_stop / etc. handlers
        with core.hooks.dispatcher. Called exactly once at process
        startup AFTER set_active_pack(). Idempotent — re-calling is a
        no-op (registrations dedup by callable identity).
        """
        ...


# ─── active-pack singleton ──────────────────────────────────────────


_ACTIVE: ContentPack | None = None


def set_active_pack(p: ContentPack) -> None:
    """Register the live content pack. Called by main.py startup BEFORE
    any guide.py request handler runs. Re-calling with a different
    pack raises — at most one active pack per process for now.
    """
    global _ACTIVE
    if _ACTIVE is not None and _ACTIVE is not p:
        raise RuntimeError(
            f"content pack already registered: {_ACTIVE.name!r}; "
            f"refusing to replace with {p.name!r} (one pack per process)"
        )
    _ACTIVE = p


def active_pack() -> ContentPack:
    """Return the registered content pack. Raises if none is set —
    the orchestrator must NOT proceed without a pack.
    """
    if _ACTIVE is None:
        raise RuntimeError(
            "no content pack registered — main.py must call "
            "set_active_pack(...) at startup before any request handler"
        )
    return _ACTIVE


def clear_active_pack_for_testing() -> None:
    """Test-only helper. Drops the singleton so a test can swap in a
    mock pack. NEVER call in production code paths."""
    global _ACTIVE
    _ACTIVE = None


__all__ = [
    "ContentPack",
    "set_active_pack",
    "active_pack",
    "clear_active_pack_for_testing",
]
