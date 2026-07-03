"""Viewer tools cluster — open_viewer (misc/pagoda3_integration.md, Tier 2).

Lets Guide hand the user a launch link for an interactive external viewer
(pagoda3) when they want to *look at* a single-cell dataset, rather than the
user having to find the file in the Files tab and click it.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_viewer_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def open_viewer(entity_id: str | None = None,
                    file_path: str | None = None,
                    viewer_id: str | None = None,
                    aba_ctx_id: str | None = None) -> dict:
        """Get a launch link for an interactive EXTERNAL viewer of a single-cell
        dataset, to show the user. Call this when the user wants to VIEW, EXPLORE,
        or visually inspect a single-cell result (an .h5ad or .lstar.zarr) — pagoda3
        opens it as an interactive UMAP / expression explorer in a new browser tab.

        Provide ONE of (if neither, the currently focused entity is used):
          • entity_id — the dataset/result entity to view (preferred).
          • file_path — a project-relative path to the data file.
        Optionally viewer_id to force a specific viewer (default: best match).

        Returns {ok, viewer_id, label, viewer_url, _agent_hint}, or {ok: False,
        error} when no external viewer applies (e.g. the file is a table/figure,
        not a viewable single-cell store — those already open inside ABA, so don't
        call this for them). On success, present `viewer_url` as a markdown link
        using the returned `label` (NOT the raw URL); the UI renders it as a launch
        button and handles the 'preparing…' step. This does not block — it only
        returns the link; conversion happens after the user clicks."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import open_viewer_impl
        return open_viewer_impl(
            {"entity_id": entity_id, "file_path": file_path, "viewer_id": viewer_id},
            peek_ctx(aba_ctx_id),
        )
