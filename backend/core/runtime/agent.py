"""Agent abstraction — Pass F (arch3_plan.md).

AgentSpec is the configurable declaration of an agent: model, prompt
path, role hint passed to the manifest assembler, allowed tools,
streaming/halt flags, iteration cap. Today's Guide and the per-advisor
sub-agents both use this; the bio/advisors/<name>.yaml file is the
spec.

For Pass F the Guide loop body still lives in guide.py (a full state-
machine extraction lands when product needs the resume path). What's
new: the spec object exists, advisor configurations move to YAML, and
loading is a `load_agent_spec(name)` call.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml


@dataclass(frozen=True)
class AgentSpec:
    name:             str                          # "guide", "skeptic", ...
    role:             str                          # "primary" | "advisor" | "background"
    model:            str
    system_prompt:    str                          # rendered text (from prompt MD file)
    manifest_role:    str                          # passed to build_manifest(role=…)
    tool_allowlist:   tuple[str, ...] = ()         # empty = no tools; ('*',) = all
    streaming:        bool = True
    halts_allowed:    bool = True
    max_iterations:   int = 20
    timeout_s:        int = 120
    fake_text:        Optional[str] = None          # canned reply for FAKE_SESSION


def _resolve_prompt(prompt_field: str, anchor_dir: Path) -> str:
    """A spec's `system_prompt` may be inline text or a path relative to the
    spec file (e.g. ../prompts/skeptic.md). Resolve and read."""
    p = (anchor_dir / prompt_field).resolve()
    if p.is_file():
        return p.read_text().strip()
    # Treat as inline if it doesn't look like a path.
    return prompt_field


def load_agent_spec(spec_path: str | Path) -> AgentSpec:
    """Load an AgentSpec from a YAML file. Path is absolute, or relative
    to the caller's content directory (callers know their layout)."""
    p = Path(spec_path)
    raw = yaml.safe_load(p.read_text()) or {}
    tools = raw.get("tool_allowlist", ())
    return AgentSpec(
        name=raw["name"],
        role=raw.get("role", "advisor"),
        model=raw.get("model", "claude-haiku-4-5-20251001"),
        system_prompt=_resolve_prompt(raw.get("system_prompt", ""), p.parent),
        manifest_role=raw.get("manifest_role", raw.get("name", "advisor")),
        tool_allowlist=tuple(tools) if isinstance(tools, (list, tuple)) else (tools,),
        streaming=bool(raw.get("streaming", False)),
        halts_allowed=bool(raw.get("halts_allowed", False)),
        max_iterations=int(raw.get("max_iterations", 8)),
        timeout_s=int(raw.get("timeout_s", 60)),
        fake_text=raw.get("fake_text"),
    )


# Spec registry — populated by content at startup via register_agent_spec.
_SPECS: dict[str, AgentSpec] = {}


def register_agent_spec(spec: AgentSpec) -> None:
    _SPECS[spec.name] = spec


def get_agent_spec(name: str) -> Optional[AgentSpec]:
    return _SPECS.get(name)


def list_agent_specs() -> list[str]:
    return sorted(_SPECS)


def filter_tools_by_allowlist(tools: list[dict], allowlist: tuple[str, ...]) -> list[dict]:
    """Respect AgentSpec.tool_allowlist:
      ()          → no tools (advisor with no tool access)
      ("*",)      → all tools pass through
      ("a","b")   → only tools whose name is in the set

    The Guide's spec uses ('*',); the existing one-shot advisors use ()
    today (they don't call tools). A future advisor that needs e.g.
    only `query_db` would set tool_allowlist: ['query_db']."""
    if not allowlist:
        return []
    if "*" in allowlist:
        return list(tools)
    keep = set(allowlist)
    return [t for t in tools if t.get("name") in keep]


def run_advisor_one_shot(spec: AgentSpec, *, user_prompt: str, max_tokens: int = 400) -> str:
    """Single-shot advisor turn — non-streaming, no tools. Mirrors the
    one-shot pattern in today's advisors.py:_ask but driven by an
    AgentSpec instead of hardcoded constants.

    The full Agent(spec).run() with streaming + tools + state-machine
    coordination is the next milestone (deferred); this one-shot covers
    every existing advisor today.
    """
    from core.config import API_KEY, FAKE_SESSION
    if FAKE_SESSION and spec.fake_text:
        return spec.fake_text
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    msg = client.messages.create(
        model=spec.model,
        max_tokens=max_tokens,
        system=spec.system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
