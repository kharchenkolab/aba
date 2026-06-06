"""Stage 6 / Phase C — agent tools for the output-cell pinning workflow.

A `cell` entity wraps an exec record so the user can keep "the view a
tool just produced" (stdout summary + figures) as evidence. The agent
should use this only when the USER explicitly asks to pin output (e.g.
"save this", "pin this cell", "keep this table summary") — not on its
own initiative.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_cell_tools(mcp: FastMCP) -> None:
    """Register pin_cell on `mcp`."""

    @mcp.tool()
    def pin_cell(exec_id: str,
                 title: str | None = None,
                 wrap_in_result: bool = True,
                 aba_ctx_id: str | None = None) -> dict:
        """Pin the output of an exec record as a `cell` entity. The
        cell's content (stdout/stderr/produced) stays in the exec
        record's JSON sidecar — the entity carries a small preview
        teaser + the exec_id pointer.

        USE THIS TOOL ONLY when the user EXPLICITLY asks to save/pin
        an output cell. Phrasings: "save this", "pin this output",
        "keep this", "remember this summary". Do NOT call on your
        own initiative; do NOT use it to "save every output". If the
        user's intent is ambiguous, ask before calling.

        Arguments:
          exec_id        — id of the exec record to pin. Find it in the
                           result of the run_python / run_r call that
                           produced the output (`result["exec_id"]`).
          title          — optional title; defaults to the first line of
                           stdout, falling back to "Output of <tool_name>".
          wrap_in_result — when True (default), the cell is also wrapped
                           in a Result via the standard pin_evidence path
                           so it shows up in Results. Set False if you
                           only want the cell entity without a Result
                           wrapper (rare).

        Returns: {cell_id, result_id, member_id}. result_id is None when
        wrap_in_result=False.
        """
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.lifecycle.cells import (
            create_cell_from_exec, pin_cell_from_exec,
        )
        ctx = peek_ctx(aba_ctx_id) or {}
        tid = ctx.get("thread_id")
        try:
            if wrap_in_result:
                return pin_cell_from_exec(exec_id, title=title, thread_id=tid)
            cell_id = create_cell_from_exec(exec_id, title=title, thread_id=tid)
            return {"cell_id": cell_id, "result_id": None, "member_id": None}
        except ValueError as e:
            return {"error": str(e)}
