"""The scientist policy: given the rendered view, choose one action.

Two implementations:
  - LLMPolicy  — Haiku 4.5 via Anthropic tool-use (Live; needs ANTHROPIC_API_KEY).
  - FnPolicy   — a plain Python function over the honest view text (zero-token;
                 used for deterministic plumbing tests and Stage-5 replay).

Both expose: act(view_text, last_observation) -> (action_name, action_input).
"""
from __future__ import annotations
from typing import Callable

from actions import TOOLS

SCIENTIST_MODEL = "claude-haiku-4-5"


class FnPolicy:
    """Drive the loop with a function fn(view, step, last_obs) -> (name, input)."""
    def __init__(self, fn: Callable):
        self.fn = fn
        self.step = 0

    def act(self, view: str, last_obs: str | None):
        action = self.fn(view, self.step, last_obs)
        self.step += 1
        return action


class LLMPolicy:
    """Haiku scientist via tool-use. Maintains its own running transcript; each
    step it's shown the current view and must pick exactly one action."""
    def __init__(self, system: str, model: str = SCIENTIST_MODEL, max_tokens: int = 1024):
        import anthropic
        self.client = anthropic.Anthropic()
        self.system = system
        self.model = model
        self.max_tokens = max_tokens
        self.messages: list[dict] = []
        self._pending_tool_id: str | None = None
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    _VIEW_TAG = "CURRENT STATE:\n"
    _SUPERSEDED = "(earlier state — superseded; act on the latest state below)"

    def _strip_stale_views(self):
        """Replace prior full-view snapshots with a stub so the transcript stops
        growing quadratically. Tool-result blocks are kept intact (they pair with
        tool_use); only the bulky superseded view text is dropped."""
        for m in self.messages:
            if m["role"] != "user":
                continue
            for b in m["content"]:
                if isinstance(b, dict) and b.get("type") == "text" \
                        and b.get("text", "").startswith(self._VIEW_TAG):
                    b["text"] = self._SUPERSEDED

    def act(self, view: str, last_obs: str | None):
        self._strip_stale_views()                    # keep only the latest view verbatim
        if self._pending_tool_id is not None:
            self.messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": self._pending_tool_id,
                 "content": last_obs or ""},
                {"type": "text", "text": self._VIEW_TAG + view}]})
        else:
            self.messages.append({"role": "user", "content": [
                {"type": "text", "text": self._VIEW_TAG + view + "\n\nChoose one action."}]})

        # Cache the static persona+tools prefix so each step re-reads it cheaply.
        system = [{"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}}]
        resp = self.client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system,
            tools=TOOLS, tool_choice={"type": "any"}, messages=self.messages)
        if getattr(resp, "usage", None):
            u = resp.usage
            self.usage["input"] += u.input_tokens or 0
            self.usage["output"] += u.output_tokens or 0
            self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
            self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.messages.append({"role": "assistant", "content": resp.content})

        tu = next((b for b in resp.content if b.type == "tool_use"), None)
        if tu is None:
            return ("done", {"summary": "(no action chosen)"})
        self._pending_tool_id = tu.id
        return (tu.name, dict(tu.input))
