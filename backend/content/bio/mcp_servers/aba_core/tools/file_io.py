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
                   root: Literal["project", "work", "data", "artifacts"] = "project",
                   max_results: int = 50,
                   aba_ctx_id: str | None = None) -> dict:
        """Glob-style file search across the project tree. Use this when
        you need to locate a file by name without remembering the exact
        path — e.g. reloading state after a kernel restart, finding a
        figure produced by an earlier Run, or just answering 'where did
        I save that?' without shelling out to `find`.

        USE THIS instead of subprocess.run(['find', ...]) inside
        run_python/run_r. It works from R too (no Python-only escape
        hatch needed) and returns structured data the agent can act on
        directly. The 2026-06-16 live bug (prj_8143327c thr_80190faf)
        burned several tool calls reaching for shell `find` to locate
        a missing seurat_integrated.rds — this tool collapses that to
        one call.

        Arguments:
          pattern      Glob like '*.rds', 'seurat_*.rds', 'umap_*.png',
                       'GSM5746259*'. Matches against the basename only
                       (the standard 'find -name' semantic). Case-sensitive.
          root         Where to search:
                         - 'project'  (default) — everything under
                           projects/<pid>/: work, data, artifacts, entities,
                           threads, etc. The widest net.
                         - 'work'     — scratch tree: run scratch dirs
                           (work/ana_*) + thread scratch
                           (work/thread-thr_*). Most common for 'where
                           did the last Run save its outputs'.
                         - 'data'     — registered DATA_DIR datasets.
                         - 'artifacts' — harvested figures / tables
                           (artifacts/<pid>/).
          max_results  Cap on matches returned (default 50). Newest
                       matches (by mtime) come first.

        Returns:
          {
            "root_path": "/abs/.../projects/<pid>",
            "pattern":   "*.rds",
            "matches": [
              {"name": "seurat_integrated.rds",
               "path": "/abs/.../work/ana_e92634df/seurat_integrated.rds",
               "size_bytes": 86_543_210,
               "mtime": "2026-06-16T13:07:42+00:00"},
              ...
            ],
            "truncated": false
          }
          On bad inputs: {"error": "..."}.
        """
        import fnmatch, os
        from datetime import datetime, timezone
        from pathlib import Path
        from core.config import (current_project_id, project_data_dir,
                                  project_work_dir, ARTIFACTS_DIR)
        # The project resolution uses the request-pinned current project
        # (#18, see chat handler) — no need for aba_ctx_id, but accept
        # it so the dispatcher's hidden-arg injection doesn't break us.
        pid = current_project_id()
        if not pid:
            return {"error": "no current project; open a project first"}
        if not pattern or "/" in pattern or pattern.startswith("."):
            return {"error":
                    f"pattern must be a basename glob (no '/' or leading "
                    f"'.'); got {pattern!r}. Examples: '*.rds', "
                    f"'umap_*.png', 'seurat_integrated.rds'."}
        max_results = max(1, min(int(max_results), 500))
        # Resolve search root.
        if root == "project":
            from core.config import PROJECTS_DIR
            search_root = PROJECTS_DIR / pid
        elif root == "work":
            search_root = project_work_dir(pid)
        elif root == "data":
            search_root = project_data_dir(pid)
        elif root == "artifacts":
            search_root = Path(ARTIFACTS_DIR) / pid
        else:
            return {"error":
                    f"root must be 'project'|'work'|'data'|'artifacts'; "
                    f"got {root!r}"}
        if not search_root.exists():
            return {"root_path": str(search_root), "pattern": pattern,
                    "matches": [], "truncated": False}

        # Walk + filter. Skip noisy + huge dirs early (.git, node_modules,
        # __pycache__, .exec sidecars — exec records aren't user data
        # the agent is looking for). dotfiles/dotdirs skipped too.
        skip_dirs = {".git", "node_modules", "__pycache__", ".exec",
                     "envs", ".cache", ".pytest_cache"}
        matches: list[dict] = []
        truncated = False
        for dirpath, dirnames, filenames in os.walk(str(search_root)):
            dirnames[:] = [d for d in dirnames
                            if d not in skip_dirs and not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                if not fnmatch.fnmatchcase(name, pattern):
                    continue
                fp = Path(dirpath) / name
                try:
                    st = fp.stat()
                except OSError:
                    continue
                matches.append({
                    "name": name,
                    "path": str(fp),
                    "size_bytes": int(st.st_size),
                    "mtime": datetime.fromtimestamp(
                        st.st_mtime, tz=timezone.utc).isoformat(),
                })
        # Newest first.
        matches.sort(key=lambda m: m["mtime"], reverse=True)
        if len(matches) > max_results:
            matches = matches[:max_results]
            truncated = True
        return {"root_path": str(search_root),
                "pattern": pattern,
                "matches": matches,
                "truncated": truncated}

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
