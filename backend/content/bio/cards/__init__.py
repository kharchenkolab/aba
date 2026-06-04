"""Bio focus-card builders. Importing this package triggers each
submodule's `register_card_builder(...)` side effect.

Also registers the thread-context renderer and policy provider with
core.manifest.assembler (Phase C.3 of misc/modularity_audit.md)."""
from . import analysis  # noqa: F401
from . import plan      # noqa: F401

from core.manifest.assembler import (
    register_thread_context_renderer,
    register_policy_provider,
)
from .thread import render_thread_context
from content.bio.lifecycle.adaptive import policy_for

register_thread_context_renderer(render_thread_context)
register_policy_provider(policy_for)
