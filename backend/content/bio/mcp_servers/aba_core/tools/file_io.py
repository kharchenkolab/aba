"""Phase 6.F — file I/O cluster (5 tools).

list_data_files (DATA_DIR enumeration), inspect_upload (file shape
sniffer), write_file / edit_file / read_file (project-scoped file ops
with safety guards: paths must stay inside DATA_DIR / WORK_DIR /
ARTIFACTS_DIR; the bio impl enforces).

write_file / edit_file / read_file take ctx for project-path
resolution (different bases per project).
"""
from __future__ import annotations

from typing import Literal

from mcp.server.fastmcp import FastMCP


def register_file_io_tools(mcp: FastMCP) -> None:
    """Register the file I/O tools on `mcp`."""

    @mcp.tool()
    def list_data_files() -> dict:
        """List files the user UPLOADED to DATA_DIR — DO NOT call
        before a download recipe (GEO/SRA/Ensembl); those recipes
        populate DATA_DIR themselves via run_python.

        Use this when the user mentions "the file" or "my data" and
        you need to find it. Returns {files, message}; if the
        message says "no datasets yet" you're not supposed to ask
        the user to upload — you're supposed to run the recipe that
        produces data."""
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
                   mode: Literal["w", "a"] = "w",
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
    def view_file(path: str, max_chars: int = 20000,
                  aba_ctx_id: str | None = None) -> dict:
        """SEE or READ an attached / on-disk file's CONTENT in your context — the
        EXPLICIT way to pull a file in (uploads do NOT auto-enter your context).
        Routes by type:
          - image -> you SEE the image (vision);
          - PDF -> its extracted text (figures not included);
          - text/code/csv/json/... -> the text (truncated to max_chars);
          - unrecognized/binary -> a hex+ascii head + a magic-byte type guess, so
            you can tell the user what it is or ask.
        For a DATA file you'll PROCESS in bulk (h5ad / fastq / large csv), use
        run_python with the path instead — view_file is for reading/seeing."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools.view_file import view_file_tool
        return view_file_tool({"path": path, "max_chars": max_chars}, peek_ctx(aba_ctx_id))

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
    def find_files(pattern: str,
                   limit: int = 50,
                   aba_ctx_id: str | None = None) -> dict:
        """Find files by NAME, wherever they live. Refer to files by the
        name your code used — the platform searches every place outputs
        exist (live session sandboxes local and remote, prior runs'
        recorded outputs including ones too large to copy, uploaded
        data, scratch) and labels each match with where it is and what
        opening it costs.

        USE THIS instead of subprocess.run(['find', ...]) inside
        run_python/run_r — one call, structured results, and it sees
        places a shell find cannot (remote sandboxes, prior runs).

        Arguments:
          pattern  Glob like '*.parquet', 'metrics_*.csv'. Matches
                   basenames and recorded relative paths. Case-sensitive.
          limit    Cap on matches (default 50).

        Returns {matches: [{name, path?, tier, locality, site?, opens,
        size_bytes?, mtime?, from_exec?}...], searched: {...the coverage
        bounds — a bounded search always says what it covered},
        unsearched?: [...tiers that could not be checked — matches there
        are UNKNOWN, not absent], truncated}. Multiple matches with the
        same name are labeled by their producing run — pick the one you
        mean; never assume the first is newest-and-right.
        """
        if not pattern or pattern.startswith("."):
            return {"error":
                    f"pattern must be a glob over names (no leading '.'); "
                    f"got {pattern!r}. Examples: '*.csv', 'metrics_*.png'."}
        from core.projects import current_project_id
        if not current_project_id():
            return {"error": "no current project; open a project first"}
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.project_locate import locate_project_files
        return locate_project_files(pattern, limit=limit,
                                    ctx=peek_ctx(aba_ctx_id))

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
