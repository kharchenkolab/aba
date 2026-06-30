"""Feedback tools cluster — build_bug_report (misc/feedback.md).

Guide calls this when the user wants to report a bug / something broke. Guide
supplies the human-readable parts; the impl stamps system facts, budgets the
email body, and returns a ready-to-click mailto: URL for the user to send.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_feedback_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def build_bug_report(headline: str,
                         what_doing: str = "",
                         diagnosis: str = "",
                         error_tail: str = "",
                         aba_ctx_id: str | None = None) -> dict:
        """Compile a concise bug report the USER emails to the ABA team, and
        return a ready-to-click `mailto:` link. Call this whenever the user wants
        to report a bug or something that went wrong.

        Provide:
          • headline    — ONE line, plain language, human-readable: what went wrong.
          • what_doing  — one line: what the user was doing when it happened.
          • diagnosis   — your terse root-cause read; may be technical/abbreviated
                          (an engineer or agent reads it to reproduce/fix).
          • error_tail  — 1–3 most-relevant error lines, if any.

        The tool stamps ABA version, OS/arch, model, and thread/focus ids, and
        enforces a strict email-size budget (so keep inputs tight — the most
        important content first). After it returns, present `mailto_url` to the
        user as a markdown link 'Review & send bug report'; it opens their mail
        client with everything prefilled and a blank space at the top for their
        own comment. They review and hit send. Replies come back to us and we can
        ask you (Guide) to pull more detail — so a tight first report is fine."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import build_bug_report_impl
        ctx = peek_ctx(aba_ctx_id)
        return build_bug_report_impl(
            {"headline": headline, "what_doing": what_doing,
             "diagnosis": diagnosis, "error_tail": error_tail},
            ctx,
        )
