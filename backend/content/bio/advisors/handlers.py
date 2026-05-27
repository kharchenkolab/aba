"""Bio advisor hook handlers (Pass D originally lived in backend/advisors.py;
relocated under bio/advisors/ as part of the T1.2 cleanup).

Importing this module registers:
  on_post_tool → fire the Methodologist asynchronously when new
                 entities were produced by an analysis run.
"""
from __future__ import annotations
import asyncio
from core.hooks.dispatcher import register
from content.bio.advisors.runner import methodologist_review


def _on_post_tool_methodologist(ctx: dict) -> None:
    if not ctx.get("new_entities"):
        return
    analysis_ctx = ctx.get("analysis_ctx") or {}
    aid = analysis_ctx.get("analysis_id")
    if not aid:
        return
    try:
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, methodologist_review, aid)
    except RuntimeError:
        methodologist_review(aid)


# Priority 20 so artifact-registration (priority 10) runs first; we
# depend on its analysis_ctx['analysis_id'] mutation.
register("on_post_tool", _on_post_tool_methodologist, priority=20)
