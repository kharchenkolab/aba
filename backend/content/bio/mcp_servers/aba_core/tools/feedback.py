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
        """Compile a bug report and return a ready-to-click `mailto:` link the
        user emails to the ABA team. Call this whenever the user wants to report a
        bug or something that went wrong.

        AUDIENCE — read this carefully: the report is read by an ABA DEVELOPER (a
        bugfixer agent or engineer) who will REPRODUCE and FIX the issue. Write it
        FOR THEM, not for the user. Capture observed behavior, how to reproduce,
        the technical state, and your root-cause hypothesis. Do NOT put user-facing
        advice or reassurance in the report (e.g. "try reloading the page") — that
        belongs in your chat reply to the user, never in the report. Be honest
        about confidence, and flag if it looks like a user-environment issue rather
        than an actual ABA defect.

        Provide:
          • headline    — ONE line: the SYMPTOM / observed failure, specific.
          • what_doing  — how to REPRODUCE: what the user did + the steps/context
                          that led to it.
          • diagnosis   — your technical root-cause hypothesis FOR THE ENGINEER:
                          what's actually happening, where (component/file), and
                          why. Terse/abbreviated is fine; note your confidence.
          • error_tail  — the 1–3 most relevant actual error/exception lines,
                          verbatim, if any.

        The tool stamps ABA version, OS/arch, model, and thread/focus ids and
        enforces a strict size budget — keep inputs tight, most important first.
        After it returns, present `mailto_url` as a markdown link
        '🐛 Review & send bug report' (not the raw URL); it opens the user's mail
        client prefilled with a blank space at the top for their own note. Replies
        route back to us and we can ask you to pull more detail, so a tight,
        accurate first report beats a padded one."""
        from core.runtime.tool_ctx import peek_ctx
        from content.bio.tools import build_bug_report_impl
        ctx = peek_ctx(aba_ctx_id)
        return build_bug_report_impl(
            {"headline": headline, "what_doing": what_doing,
             "diagnosis": diagnosis, "error_tail": error_tail},
            ctx,
        )
