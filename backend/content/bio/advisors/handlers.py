"""Bio advisor hook handlers (Pass D originally lived in backend/advisors.py;
relocated under bio/advisors/ as part of the T1.2 cleanup).

Importing this module registers:
  on_post_tool → fire the Methodologist asynchronously when new
                 entities were produced by an analysis run.
"""
from __future__ import annotations
from core.hooks.dispatcher import register
from content.bio.advisors.runner import methodologist_review


def _on_post_tool_methodologist(ctx: dict) -> None:
    if not ctx.get("new_entities"):
        return
    analysis_ctx = ctx.get("analysis_ctx") or {}
    aid = analysis_ctx.get("analysis_id")
    if not aid:
        return
    # B4: link the Methodologist's Turn row back to the Guide turn that
    # produced the entities. thread_id rides along for inheritance.
    parent_run_id = ctx.get("parent_run_id")
    thread_id = ctx.get("thread_id")

    # projects.spawn keeps the project binding across the thread hop (the review
    # writes into this project's graph) and owns the no-loop inline fallback.
    from core import projects
    projects.spawn(methodologist_review, aid,
                   parent_run_id=parent_run_id, thread_id=thread_id)


# Priority 20 so artifact-registration (priority 10) runs first; we
# depend on its analysis_ctx['analysis_id'] mutation.
register("on_post_tool", _on_post_tool_methodologist, priority=20)
