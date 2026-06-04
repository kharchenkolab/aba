"""Bio-prompt registrations.

Registers prompts the platform needs to fetch by name (currently:
"thread_summary" used by core/summarize/budget_summary.py). The
provider is the existing `_load_annotation_prompt` loader; this module
just wires it into core's `core.prompts` registry.

Imported from `content/bio/__init__.py` for the registration side-effect.
"""
from __future__ import annotations

from core import prompts as _core_prompts
from content.bio.lifecycle.promote import _load_annotation_prompt


def _thread_summary() -> str:
    return _load_annotation_prompt("thread_summary")


_core_prompts.register("thread_summary", _thread_summary)
