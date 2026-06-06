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
                      aba_ctx_id: str | None = None) -> dict:
        """Create a revision of an existing figure or table by re-running
        modified code. The new artifact is linked to the original via a
        wasRevisionOf edge so both stay pinned siblings — the user can
        navigate between them via the chevron arrows in the focus view.

        USE THIS TOOL ONLY when the user EXPLICITLY asks for a variant
        of an existing figure/table — wording like "make a revision",
        "what if we changed X", "try with a tighter cutoff", "redo this
        but with…". Do NOT call this on your own initiative; do NOT use
        it to "improve" a figure unprompted. If the user's intent is
        ambiguous, ask before calling.

        Arguments:
          entity_id      — id of the figure/table to revise (NOT the
                           Result wrapping it; pass the underlying
                           evidence entity).
          modified_code  — the full revised code to run. It should be a
                           tweak of the original (which you can fetch
                           via the entity's exec record); keep variable
                           names + structure so the diff is meaningful.
          title          — optional title for the new revision; defaults
                           to the parent's title.

        Returns: {new_entity_id, exec_id, wasRevisionOf, produced}.
        Raises if the parent doesn't exist, isn't a figure/table, or
        the modified code produced no artifacts.
        """
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.lifecycle.revisions import make_revision as _make
        ctx = peek_ctx(aba_ctx_id) or {}
        try:
            return _make(
                entity_id, modified_code, title=title,
                thread_id=ctx.get("thread_id"),
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
