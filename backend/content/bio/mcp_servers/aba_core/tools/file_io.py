"""Phase 6.F — file I/O cluster (5 tools).

list_data_files (DATA_DIR enumeration), inspect_upload (file shape
sniffer), write_file / edit_file / read_file (project-scoped file ops
with safety guards: paths must stay inside DATA_DIR / WORK_DIR /
ARTIFACTS_DIR; the bio impl enforces).

write_file / edit_file / read_file take ctx for project-path
resolution (different bases per project).
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_file_io_tools(mcp: FastMCP) -> None:
    """Register the file I/O tools on `mcp`."""

    @mcp.tool()
    def list_data_files() -> dict:
        """List files in the project DATA_DIR. Use to discover the
        filenames you'll need to pass to read_csv_info / register_dataset."""
        from content.bio.tools import list_data_files as _impl
        return _impl({})

    @mcp.tool()
    def inspect_upload(path: str) -> dict:
        """Sniff the shape of an uploaded file or folder — file type,
        column hint, layout hint (single-file vs 10x triplet vs other
        bundle). Used before register_dataset to surface what's there."""
        from content.bio.tools import inspect_upload as _impl
        return _impl({"path": path})

    @mcp.tool()
    def write_file(path: str, body: str,
                   mode: str = "text",
                   overwrite: bool = False,
                   aba_ctx_id: str | None = None) -> dict:
        """Write a file inside the project workspace. Paths must
        resolve inside DATA_DIR / WORK_DIR / ARTIFACTS_DIR — absolute
        paths outside the project are refused."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import write_file_tool
        return write_file_tool(
            {"path": path, "body": body, "mode": mode, "overwrite": overwrite},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def edit_file(path: str, old_string: str, new_string: str,
                  replace_all: bool = False,
                  aba_ctx_id: str | None = None) -> dict:
        """Exact-string replace inside an existing project file.
        `old_string` must be unique (or pass `replace_all=true`)."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import edit_file_tool
        return edit_file_tool(
            {"path": path, "old_string": old_string,
             "new_string": new_string, "replace_all": replace_all},
            peek_ctx(aba_ctx_id),
        )

    @mcp.tool()
    def read_file(path: str,
                  offset: int | None = None,
                  limit: int | None = None,
                  aba_ctx_id: str | None = None) -> dict:
        """Read a project file. Optionally slice via offset+limit
        (line-based). Returns the body with 1-based line numbers."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import read_file_tool
        return read_file_tool(
            {"path": path, "offset": offset, "limit": limit},
            peek_ctx(aba_ctx_id),
        )
