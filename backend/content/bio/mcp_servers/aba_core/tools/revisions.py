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
                      language: str | None = None,
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
          language         — 'python' or 'r'. Optional override; when
                             omitted, the language is detected from
                             modified_code (Python signals like `import`,
                             `def` → python; R signals like `library(`,
                             `<-`, `ggplot` → r). Use the override only
                             when the auto-detection is wrong (e.g.,
                             reticulate-style mixed snippet).
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
        lang = (language or "").lower().strip() or None
        if lang and lang not in ("python", "r"):
            return {"error":
                    f"language must be 'python' or 'r', got {language!r}"}
        try:
            return _make(
                entity_id, modified_code, title=title,
                language=lang,
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
    def diff_env(entity_id: str, aba_ctx_id: str | None = None) -> dict:
        """Show which packages CHANGED between the environment that produced this
        figure/table and the CURRENT environment. Read-only. USE THIS when a
        reproduction unexpectedly differs and you need to understand WHY (which
        dependency drifted). Returns {added, removed, changed, n_changed, note}."""
        from content.bio.lifecycle.revisions import diff_env as _diff
        try:
            return _diff(entity_id)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def rebuild_env(entity_id: str, only: list | None = None,
                    aba_ctx_id: str | None = None) -> dict:
        """RARE escape hatch: reconstruct a throwaway isolated env pinned to the
        package versions this figure was originally made with, to investigate a
        reproduction discrepancy. Pass `only=[pkg,...]` to rebuild just a few
        packages (bisect a drift gradually — a full rebuild is slow and may
        conflict), then run code via run_python(env=<the returned name>). Use ONLY
        when chasing a real difference; the default is always the current env."""
        from content.bio.lifecycle.revisions import rebuild_env as _rebuild
        try:
            return _rebuild(entity_id, only=only)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def export_reproduction_bundle(entity_id: str, aba_ctx_id: str | None = None) -> dict:
        """Write a portable reproduction bundle for this figure/table — the
        producing code, a pinned requirements lock, and the full provenance record
        — to a directory. USE THIS when the user needs to reproduce 'for the
        record' (a journal/policy requirement). Returns {bundle_dir, files}."""
        from content.bio.lifecycle.revisions import export_bundle as _export
        try:
            return _export(entity_id)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def list_revisions(entity_id: str,
                       aba_ctx_id: str | None = None) -> dict:
        """List the full revision chain for a figure/table with
        user-facing version numbers (v1 = oldest, vN = newest = the
        currently-displayed one). The labels match the chevrons under
        the focused Result so the agent can translate the user's
        "version N" / "vN" reference directly to an entity id.

        USE THIS WHENEVER the user mentions a figure/table by version
        number ("go back to v6", "remove version 3", "compare v2 vs
        v4") — call list_revisions first to map the version label to
        a concrete entity id, then pass that id to read_entity,
        view_artifact, set_current_revision, delete_revision, or
        make_revision. Beats get_provenance for this purpose: depth-
        unbounded, includes exec_id, and counts from the oldest.

        Accepts ANY id in the chain (head, anchor, or anything in
        between) — the chain is resolved bidirectionally.

        Arguments:
          entity_id — any figure/table id in the chain.

        Returns: {total, current_id, revisions: [{version, id, title,
        created_at, exec_id, is_current}, ...]} ordered newest first
        (matching the chevron strip in the UI). Returns
        {"error": "..."} when the entity is unknown or wrong type.
        """
        from content.bio.lifecycle.revisions import list_revisions as _ls
        try:
            return _ls(entity_id)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def set_current_revision(entity_id: str,
                             aba_ctx_id: str | None = None) -> dict:
        """Make `entity_id` the displayed/current revision in its chain
        WITHOUT destroying any other revision.

        USE THIS (not delete_revision) when the user wants to "go back
        to v3", "show version 4", "make v6 the current one", or any
        non-destructive revision switch. Newer revisions are flipped
        to status='superseded' so they vanish from the chevron strip
        but stay on disk and queryable; older revisions stay active.
        Fully reversible: another set_current_revision call promotes
        whatever the user wants next.

        Pair with list_revisions:
          revs = list_revisions(<any chain id>)        # → v1…vN map
          set_current_revision(revs[idx_for_v6].id)    # switch

        Prefer set_current_revision over delete_revision unless the
        user explicitly asks to DELETE a revision permanently.

        Arguments:
          entity_id — id of the figure/table revision to make current.

        Returns: {current_id, total_in_chain, superseded: [...],
        restored: [...], re_anchored_members: [...]} on success;
        {"error": "..."} on bad inputs.
        """
        from content.bio.lifecycle.revisions import set_current_revision as _set
        try:
            return _set(entity_id)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def delete_revision(entity_id: str,
                        aba_ctx_id: str | None = None) -> dict:
        """Hard-delete a single figure/table revision (PERMANENT).
        Children re-parent to this revision's parent; if the deleted
        entity was the chain anchor that a Result member.ref points
        at, the member is re-anchored to the next-oldest active
        descendant.

        USE THIS ONLY when the user wants to PERMANENTLY THROW AWAY a
        specific version — e.g. "delete this revision", "wipe v3",
        "remove this version forever".

        For "go back to vN", "come back to v6", "show version 4",
        "make v2 the current one", or any NAVIGATION between
        revisions, use `set_current_revision` instead — it's
        non-destructive (the hidden revisions stay on disk and the
        user can switch back).

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
