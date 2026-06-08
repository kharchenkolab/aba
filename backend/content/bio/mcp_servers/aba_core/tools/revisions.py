"""Phase 5 of misc/exec_records_and_versioning.md — agent tools for the
revisions workflow.

Two tools that wrap the lifecycle helpers in
`content/bio/lifecycle/revisions.py`. Both must be used **only when the
user explicitly asks** for a code variant or a reproduction — the
docstrings spell this out so the agent doesn't speculate. The pre-tool
veto remains the fallback if the agent ignores the docstring; a stronger
guardrail can be added later as a hook (see #307 / #322 for precedent).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_revision_tools(mcp: FastMCP) -> None:
    """Register make_revision + reproduce_from_exec on `mcp`."""

    @mcp.tool()
    def make_revision(entity_id: str, modified_code: str,
                      title: str | None = None,
                      supersede_newer: bool = False,
                      aba_ctx_id: str | None = None) -> dict:
        """Create a revision of an existing figure or table by re-running
        modified code. The new artifact is linked to the original via a
        wasRevisionOf edge so both stay pinned siblings — the user can
        navigate between them via the chevron arrows in the focus view.

        USE THIS TOOL whenever the user asks for any MODIFIED VERSION
        of an existing focused figure/table. Triggering wording covers
        FOUR shapes — all are revisions:

          - content change: "what if we changed X", "try with a tighter
            cutoff", "redo with the day-7 sample", "remove this cluster"
          - layout change: "put the legend on the bottom", "panel
            arrangement", "Nature-style", "two-column figure"
          - style change: "higher DPI", "bigger fonts", "no grid",
            "color-blind palette"
          - format change: "PDF version", "render as SVG", "EPS for
            print", "high-res PNG"

        Any derivative rendering of a focused figure goes through this
        tool. The new rendering is pinned as a sibling revision in the
        figure's wasRevisionOf chain, surfaced via the chevron arrows
        on the focused Result so the user can navigate between versions
        and download the canonical (PDF/SVG/PNG) directly. Without
        this tool, a one-off run_python / run_r writes an orphan file
        the user has to dig out of chat — they LOSE the chain context,
        the panel preview, and the audit trail.

        Do NOT call this on your own initiative; do NOT use it to
        "improve" a figure unprompted. With a CLEAR focused figure/
        table (or a Result holding one) + "modified version" wording,
        just call this. Do not first ask the user whether to make a revision
        or leave the original alone — that's the chain's whole point
        (the original stays as a sibling). If focus is genuinely
        ambiguous (no figure focused, or two equally-focused candidates
        from prior turns), ask before calling.

        Arguments:
          entity_id        — id of the figure/table to revise (NOT the
                             Result wrapping it; pass the underlying
                             evidence entity).
          modified_code    — the full revised code to run. It should be a
                             tweak of the original (which you can fetch
                             via the entity's exec record); keep variable
                             names + structure so the diff is meaningful.
          title            — optional title for the new revision; defaults
                             to the parent's title.
          supersede_newer  — when True, accepts revising from a non-latest
                             revision by marking any currently-newer
                             revisions as status='superseded'. Default
                             False: refuses (returns {"error": "...,
                             newer entries: [...]"}). The UI uses this
                             refusal as the trigger for a confirmation
                             dialog; the agent should default to False
                             too and only retry with True if the user
                             explicitly confirms.

        Returns on success: {new_entity_id, exec_id, wasRevisionOf,
        superseded, produced}.
        Returns {"error": "..."} on bad inputs.
        """
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.lifecycle.revisions import make_revision as _make
        ctx = peek_ctx(aba_ctx_id) or {}
        try:
            return _make(
                entity_id, modified_code, title=title,
                thread_id=ctx.get("thread_id"),
                supersede_newer=supersede_newer,
            )
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def reproduce_from_exec(entity_id: str,
                            aba_ctx_id: str | None = None) -> dict:
        """Re-run the exec that produced this figure/table and report
        the result. Does NOT create a new entity — just runs the
        original code in the current kernel and returns a summary
        including any env_fingerprint drift since the original run.

        USE THIS when the user asks to "reproduce", "re-run", or "verify
        that this still works" — it's a safe, deterministic check. The
        agent may also call this if the user is unsure whether a figure
        from a prior session still represents the current code path
        (drift detection is the value here).

        Arguments:
          entity_id — id of the figure/table to reproduce.

        Returns: {reproduced (bool), new_exec_id, env_drift (bool),
        original_fingerprint, new_fingerprint, produced, warnings, error}.
        On env_drift=True, warn the user that numerical details may
        differ from the original run.
        """
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.lifecycle.revisions import reproduce_from_exec as _repro
        ctx = peek_ctx(aba_ctx_id) or {}
        try:
            return _repro(entity_id, thread_id=ctx.get("thread_id"))
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def delete_revision(entity_id: str,
                        aba_ctx_id: str | None = None) -> dict:
        """Hard-delete a single figure/table revision while keeping the
        chain intact. Children (newer revisions pointing at this one)
        re-parent to this revision's parent; if the deleted entity was
        the chain anchor that a Result member.ref points at, the member
        is re-anchored to the next-oldest active descendant.

        USE THIS when the user wants to throw away a specific version
        of a figure/table (e.g. "delete this revision", "remove v3",
        "roll back: I want to keep only v1 and v2"). The chain stays
        navigable; the deleted entity is gone permanently.

        DO NOT use this to remove a figure FROM a Result without
        deleting the figure itself — that's `remove_result_member`
        (HTTP) or the entity menu's "Remove from Result" gesture.

        Refuses when `entity_id` is the only active version in its
        chain (returns an error pointing at the right alternative).

        Arguments:
          entity_id — id of the figure/table revision to delete.

        Returns: {deleted, new_anchor, new_parent,
        re_parented_children: [...], re_anchored_members: [...]} on
        success; {"error": "..."} on entity-not-found / wrong type /
        single-version chain.
        """
        from content.bio.lifecycle.revisions import delete_revision as _del
        try:
            return _del(entity_id)
        except ValueError as e:
            return {"error": str(e)}
