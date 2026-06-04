"""Bio content pack.

Importing this package wires up the bio-specific registrations:
  - per-entity-type focus card builders (bio/cards/)
  - the advisor specs + handlers (bio/advisors/)
  - skill names the plan validator references (bio/skills/catalog.py)
"""
# Phase 4.2: load bio's declarative entity-type YAMLs FIRST so the
# registry is populated before any other bio module that might query it.
from pathlib import Path as _Path
from core.entity_types.registry import load_types as _load_entity_types
_load_entity_types(_Path(__file__).parent / "entity_types")

from . import cards          # noqa: F401  — registers per-type card builders
from . import advisors       # noqa: F401  — loads YAML specs + handlers
from . import skills         # noqa: F401  — registers known skills
from . import capabilities   # noqa: F401  — registers the capability seed provider
from .files import layout    # noqa: F401  — registers per-type display_path computers
from . import viewers        # noqa: F401  — loads file-viewer registry
from . import prompts        # noqa: F401  — registers named prompts (e.g. thread_summary)

# Wire on_project_open hook for bio's display-path backfill (Phase C.4).
from core.hooks.dispatcher import register as _register_hook
from .graph.display import _on_project_open as _display_on_project_open
_register_hook("on_project_open", _display_on_project_open, priority=10)
