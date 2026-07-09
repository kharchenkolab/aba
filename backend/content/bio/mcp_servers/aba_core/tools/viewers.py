"""Viewer tools cluster — open_viewer (misc/pagoda3_integration.md, Tier 2).

Lets Guide hand the user a launch link for an interactive external viewer
(pagoda3) when they want to *look at* a single-cell dataset, rather than the
user having to find the file in the Files tab and click it.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_viewer_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def get_viewer_url(entity_id: str | None = None,
                       path: str | None = None,
                       viewer_id: str | None = None,
                       aba_ctx_id: str | None = None) -> dict:
        """Construct a launch link (URL) for an interactive EXTERNAL viewer of a
        single-cell dataset, and return it to show the user — this does NOT open
        anything itself; it hands back a `viewer_url` the chat renders as a button.
        Call it when the user wants to VIEW, EXPLORE, or visually inspect a
        single-cell result (an .h5ad or .lstar.zarr) — pagoda3 then opens it as an
        interactive UMAP / expression explorer in a new browser tab when clicked.

        Provide ONE of (if neither, the currently focused entity is used):
          • entity_id  — a dataset/result entity to view (preferred when it exists).
          • path       — the data file. A bare filename ('processed.h5ad') is fine —
                         it's resolved against the project's files (basename or
                         partial path); you do NOT need the full tree path.
        Optionally viewer_id to force a specific viewer (default: best match).

        Returns {ok: true, label, viewer_url, resolved_path, _agent_hint} on
        success, or {ok: false, error} when the file can't be found or no viewer
        applies (a figure/table/PDF/CSV opens inside ABA already — don't call this
        for those).

        CONTRACT — read `ok`:
          • ok:true  → present `viewer_url` as a markdown link using `label`, e.g.
            `[Explore in pagoda3](<viewer_url>)` — NOT the raw URL, no emoji (the UI
            draws the button). It opens a new tab and handles the 'preparing…' step;
            this call does NOT block or convert.
          • ok:false → do NOT fabricate or hand out a link. Tell the user what
            `error` says, or retry with a corrected file (the error lists matching
            files when it can). A returned link is ALWAYS validated to resolve."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import open_viewer_impl
        return open_viewer_impl(
            {"entity_id": entity_id, "file_path": path, "viewer_id": viewer_id},
            peek_ctx(aba_ctx_id),
        )
