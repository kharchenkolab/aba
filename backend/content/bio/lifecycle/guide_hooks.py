"""Bio reactions to Guide-fired hook events.

W1-A.2 phase 4': guide.py used to lazy-import from content.bio.* at
six sites inside stream_response (close_run, open_run + _feedlog,
set_plan_lifecycle ×2 for plan completion/failure, active_run_id for
background jobs, figure_history ×2 for focus markers). Each site now
fires a generic event via core.hooks.dispatcher.dispatch; the handlers
below run those reactions on bio's side. Removing the lazy imports
drops the audit-gate test `test_guide_lazy_bio_imports_are_gated` from
9 to 0 — a hypothetical content/legal/ vertical can now slot in
without any guide.py edits, completing audit-#7's intent.

The events:
- on_run_save_opt_out      side-effect — user opted out of saving the
                            run; bio closes the open Run entity.
                            ctx: {thread_id}
- on_plan_presented         side-effect — present_plan validated; bio
                            opens a Run server-side (default-save).
                            ctx: {thread_id, plan_title,
                            focus_entity_id, plan_entity_id}
- on_plan_complete          side-effect — plan-driving turn finished
                            normally; bio sets lifecycle='completed'.
                            ctx: {plan_entity_id}
- on_plan_failed            side-effect — plan-driving turn errored;
                            bio sets lifecycle='failed'.
                            ctx: {plan_entity_id}
- on_background_job_submit  query — guide.py needs the active_run_id
                            to stamp on a background job. Bio writes
                            it back via ctx['active_run_id'].
                            ctx in:  {thread_id}
                            ctx out: {active_run_id}
- on_resolve_displayed_id   query — focus marker / trailer wants the
                            displayed id for a figure ref (latest in
                            its revision chain, if any). Bio overrides
                            ctx['displayed_id'] when a chain exists.
                            ctx in:  {ref, displayed_id (fallback)}
                            ctx out: {displayed_id}
"""
from __future__ import annotations

from core.hooks.dispatcher import register

# Direct imports here — this module IS bio, so importing bio
# submodules is the whole point. The platform-purity guard is on
# guide.py, not on bio's internal layout.
from content.bio.graph.figure_history import figure_history
from content.bio.lifecycle.plans import set_plan_lifecycle
from content.bio.lifecycle.runs import (active_run_id, close_run, open_run,
                                         retain_run_keepers, run_id_for_plan)
from content.bio.tools import _feedlog


def _on_run_save_opt_out(ctx: dict) -> None:
    """User's Go message carries 'do not save this as a run'. Close the
    just-opened (still-empty) Run before execution groups under it."""
    tid = ctx.get("thread_id")
    if not tid:
        return
    try:
        close_run(tid)
    except Exception:  # noqa: BLE001 — opt-out is best-effort
        pass


def _on_plan_presented(ctx: dict) -> None:
    """Open the analysis Run for this plan NOW, server-side — the
    default-save the user expects, robust even if the agent never calls
    open_run. Rotates any prior open Run; an empty one is discarded on
    the next rotation or on the unchecked-box opt-out."""
    tid = ctx.get("thread_id")
    title = ctx.get("plan_title") or "Analysis run"
    feid = ctx.get("focus_entity_id")
    plan_eid = ctx.get("plan_entity_id")
    try:
        rid = open_run(tid, title, focus_entity_id=feid, plan_entity_id=plan_eid)
        _feedlog(f"SERVER open_run @present_plan title={title!r} "
                 f"plan_eid={plan_eid} -> run={rid}")
    except Exception as e:  # noqa: BLE001
        try:
            _feedlog(f"SERVER open_run @present_plan FAILED: {e}")
        except Exception:  # noqa: BLE001 — log is best-effort
            pass


def _on_plan_complete(ctx: dict) -> None:
    """#160: when this turn was driving a plan's execution, mark the plan completed, and
    retain the Run's keepers NOW (plan-end) so durability + the Files panel are ready
    promptly instead of waiting for Run-close (which we delay for follow-ups). Both are
    idempotent + best-effort."""
    eid = ctx.get("plan_entity_id")
    if not eid:
        return
    try:
        set_plan_lifecycle(eid, "completed")
    except Exception:  # noqa: BLE001 — plan-tracking is best-effort
        pass
    # Retain the Run's keepers at plan-end. guide dispatches on_plan_complete with only
    # plan_entity_id, so resolve the Run from that (thread_id is a fallback if present).
    try:
        tid = ctx.get("thread_id")
        rid = run_id_for_plan(eid) or (active_run_id(str(tid)) if tid else None)
        if rid:
            retain_run_keepers(rid)
            _feedlog(f"SERVER retain@plan_complete run={rid}")
    except Exception as e:  # noqa: BLE001 — retain must never break the turn
        _feedlog(f"SERVER retain@plan_complete FAILED: {e}")


def _on_plan_failed(ctx: dict) -> None:
    """Mark plan failed when its driving turn errors out."""
    eid = ctx.get("plan_entity_id")
    if not eid:
        return
    try:
        set_plan_lifecycle(eid, "failed")
    except Exception:  # noqa: BLE001
        pass


def _on_background_job_submit(ctx: dict) -> None:
    """run_python(background=True): fill ctx['active_run_id'] so the
    job row carries the right run context for Phase-C continuation
    (live bug 2026-06-05 — without this the job row's params carry
    run_id=null and continuation can't decide where to fire)."""
    tid = ctx.get("thread_id")
    if not tid:
        return
    try:
        ctx["active_run_id"] = active_run_id(str(tid))
    except Exception:  # noqa: BLE001 — leave ctx['active_run_id'] as-is on failure
        pass


def _on_resolve_displayed_id(ctx: dict) -> None:
    """If the entity ref has a figure-history chain, the displayed id
    is chain[0] (the latest revision). The focus-marker / trailer cite
    that id so the agent uses the displayed entity, not the anchor.

    No-op when the ref has no chain — ctx['displayed_id'] stays at
    whatever the caller seeded it with (typically the input ref)."""
    ref = ctx.get("ref")
    if not ref:
        return
    try:
        chain = figure_history(ref)
        if chain:
            ctx["displayed_id"] = chain[0]["id"]
    except Exception:  # noqa: BLE001
        pass


# Side-effect registration on import — BioPack.register_hooks() pulls
# this module in. The hook dispatcher dedupes by callable identity, so
# a re-import (pytest re-runs, dev reload) is a no-op.
register("on_run_save_opt_out", _on_run_save_opt_out)
register("on_plan_presented", _on_plan_presented)
register("on_plan_complete", _on_plan_complete)
register("on_plan_failed", _on_plan_failed)
register("on_background_job_submit", _on_background_job_submit)
register("on_resolve_displayed_id", _on_resolve_displayed_id)
