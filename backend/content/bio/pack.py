"""BioPack — bio's implementation of the core.runtime.content_pack.ContentPack
protocol.

This is the SINGLE module the platform imports from bio at startup. It
gives the orchestrator (guide.py) access to bio's prompts, tools,
card builders, and hook handlers without guide.py importing
content.bio.* directly.

Audit context: misc/modularity_audit.md §3.4 + refactoring2.md §6.1
(Track A.3). The audit's #7 success criterion ("hypothetical
content/legal/ could slot in") was PARTIAL because guide.py imported
content.bio.* at the top. Wave 1 A.3 lifts those imports here; with
this module landed, guide.py imports `from core.runtime.content_pack
import active_pack` and queries the pack — no direct bio reference.

Side-effect note: importing `content.bio` (the package) triggers the
existing registrations in content/bio/__init__.py — entity-type YAML
loading, card builders, advisor specs, capability seeds, file viewers,
named prompts, on_project_open hook. BioPack.register_hooks() also
imports the per-hook modules that aren't auto-imported (lifecycle
registry, advisor handlers, adaptive reflection, proposals
scheduler) — those used to be guide.py-side noqa imports.
"""
from __future__ import annotations

from typing import Any, Callable


class BioPack:
    """The bio content pack. Single-instance per process.

    Constructed eagerly when `content.bio` is imported (see
    bio/__init__.py's BIO_PACK = BioPack()). main.py calls
    set_active_pack(BIO_PACK) + BIO_PACK.register_hooks() at startup.
    """

    name = "bio"

    def __init__(self) -> None:
        # Hook registration is idempotent — re-calling register_hooks()
        # re-imports the modules; the dispatcher's register() dedupes
        # by callable identity. This flag is for observability only.
        self._hooks_registered = False

    # ─── ContentPack protocol surface ───────────────────────────────

    def prompts(self) -> dict[str, Callable[..., Any]]:
        """Named prompt builders the orchestrator looks up by name.

        Today the orchestrator uses 'system' and 'recipes_reminder'.
        'focus_preamble' is consumed by the manifest assembler, not
        guide.py — included here so a future caller doesn't have to
        rediscover the import path.
        """
        from content.bio.prompts.build import (build_system,
                                                build_recipes_reminder,
                                                build_discovery_reminder)
        from core.manifest.assembler import render_focus_preamble
        return {
            "system":             build_system,
            "recipes_reminder":   build_recipes_reminder,
            "discovery_reminder": build_discovery_reminder,
            "focus_preamble":     render_focus_preamble,
        }

    def tools(self) -> list[dict]:
        """Tool schemas in Anthropic shape. Today: bio.tools.TOOL_SCHEMAS."""
        from content.bio.tools import TOOL_SCHEMAS
        return TOOL_SCHEMAS

    def execute_tool(self) -> Callable[..., Any]:
        """Per-tool dispatcher. Same signature as bio.tools.execute_tool:
        (tool_name: str, tool_input: dict, ctx: dict | None) -> dict (or coroutine)."""
        from content.bio.tools import execute_tool
        return execute_tool

    def cards(self) -> dict[str, Callable[..., Any]]:
        """Per-entity-type card builders for the manifest assembler.

        Bio's cards register into core.manifest.assembler._BUILDERS at
        import time (via bio/__init__.py's `from . import cards`).
        Returning a snapshot of the registry lets callers inspect
        what's available without importing bio directly.
        """
        from core.manifest import assembler as _asm
        return dict(_asm._BUILDERS)

    def register_hooks(self) -> None:
        """Trigger bio's hook-handler module imports.

        Idempotent: re-calling re-runs the imports (cheap — Python's
        module cache short-circuits). The hook dispatcher's
        register() dedupes by callable identity, so a duplicate
        registration is a no-op.

        These modules used to live as noqa: F401 imports at the top
        of guide.py. Moving them here removes the last bio coupling
        from the orchestrator.
        """
        import content.bio.lifecycle.registry    # noqa: F401  on_post_tool
        import content.bio.advisors               # noqa: F401  handlers + specs
        import content.bio.lifecycle.adaptive    # noqa: F401  on_stop: maybe_reflect
        import content.bio.proposals.scheduler   # noqa: F401  on_stop: evaluate_thread
        # W1-A.2 phase 4': bio reactions for events that guide.py used to
        # call inline via lazy imports (close_run, open_run, _feedlog,
        # set_plan_lifecycle, active_run_id, figure_history). Each is
        # now an `on_*` event fired by guide.py; this module registers
        # the bio side.
        import content.bio.lifecycle.guide_hooks  # noqa: F401
        self._hooks_registered = True

    # ─── Bio-specific helpers exposed for orchestrator convenience ──

    def new_session_id(self) -> str:
        """Generate a fresh session ID. Bio's format is `sess_<10hex>`;
        a future pack could override. The orchestrator uses this to
        tag per-turn telemetry."""
        from content.bio.lifecycle.adaptive import new_session_id
        return new_session_id()


# Module-level singleton. Importing this module DOES instantiate; the
# constructor is side-effect-free aside from the flag. main.py calls
# set_active_pack(BIO_PACK) at startup.
BIO_PACK = BioPack()

__all__ = ["BioPack", "BIO_PACK"]
