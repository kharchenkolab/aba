"""
Scenarios — variants of an existing figure produced by re-running the
baseline's producing_code with substituted parameters.

End-user mental model: the word "branch" never appears. The user says
"what if we use a tighter mt_fraction cutoff?" and a scenario variant
appears alongside the baseline, with a Compare toggle in the canvas.

This module is the bottom half: given an explicit description (or new
code), it rewrites the baseline's code (via Haiku) and runs it.
"""
from __future__ import annotations
import json
import re
from typing import Literal, Optional

from config import API_KEY, MODEL, FAKE_SESSION
from core.graph.edges import add_edge
from core.graph.entities import create_entity, get_entity
from content.bio.tools import execute_tool
from content.bio.lifecycle.registry import _title_from_code


Language = Literal["r", "python"]


# Strong R signals (Python won't emit any of these in a script context).
_R_SIGNALS = (
    re.compile(r"^\s*library\s*\(", re.M),
    re.compile(r"<-\s"),
    re.compile(r"\bggsave\s*\("),
    re.compile(r"\bggplot\s*\("),
    re.compile(r"%>%"),
    re.compile(r"\bSeurat\b|\bSignac\b"),
)

# Strong Python signals.
_PY_SIGNALS = (
    re.compile(r"^\s*import\s+\w", re.M),
    re.compile(r"^\s*from\s+[\w.]+\s+import\s+", re.M),
    re.compile(r"\bplt\.savefig\s*\("),
    re.compile(r"\bsc\.(pl|tl|pp)\.\w+"),
    re.compile(r"^\s*def\s+\w+\s*\(", re.M),
)


def _detect_language(code: str) -> Language:
    """Sniff the language of a producing_code snippet.

    Strong R signals (library(), <-, ggsave/ggplot, Seurat) outweigh
    Python signals. Defaults to python when ambiguous — that matches
    the historical behavior + the fact that scanpy is the more common
    scrna recipe language in the catalogue.
    """
    if not code:
        return "python"
    r_hits = sum(1 for p in _R_SIGNALS if p.search(code))
    py_hits = sum(1 for p in _PY_SIGNALS if p.search(code))
    # R `library(`, Python `import` are unambiguous single signals.
    if r_hits > 0 and py_hits == 0:
        return "r"
    if py_hits > 0 and r_hits == 0:
        return "python"
    # Mixed (rare; can happen if R code is embedded in a Python reticulate
    # block) — go by majority, tiebreak to python.
    return "r" if r_hits > py_hits else "python"


def _rewrite_system_prompt(language: Language) -> str:
    if language == "r":
        return (
            "You are rewriting a small R analysis script to produce a scenario "
            "variant. The user will describe a change (e.g. 'use mt_fraction "
            "cutoff 0.10' or 'exclude sample S4'). Output ONLY the modified R "
            "code — no commentary, no markdown fences, no preamble. The code "
            "must continue to save its figure with ggsave() (or "
            "png()/dev.off() for base-grid plots) so the harness captures it. "
            "Keep all variable names and structure that aren't part of the "
            "change."
        )
    return (
        "You are rewriting a small Python analysis script to produce a "
        "scenario variant. The user will describe a change (e.g. 'use "
        "mt_fraction cutoff 0.10' or 'exclude sample S4'). Output ONLY the "
        "modified Python code — no commentary, no markdown fences, no "
        "preamble. The code must continue to save its figure with "
        "plt.savefig() (or sc.pl.*(save=...)) so the harness captures it. "
        "Keep all variable names and structure that aren't part of the "
        "change."
    )


# Back-compat for any external import; deprecated — use _rewrite_system_prompt.
_REWRITE_SYSTEM = _rewrite_system_prompt("python")


def _rewrite_code_via_llm(original_code: str, description: str,
                          language: Language = "python") -> str:
    """One-shot Haiku call to rewrite a script for a scenario variant.

    The system prompt + the fenced markdown hint passed to the model are
    both keyed to `language` so the rewrite stays in the same language as
    the baseline.
    """
    if FAKE_SESSION:
        raise RuntimeError(
            "scenario rewrite needs the live LLM; pass `code` directly in fake mode",
        )
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    fence = "r" if language == "r" else "python"
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=_rewrite_system_prompt(language),
        messages=[{
            "role": "user",
            "content": (
                f"Modify this code for the following scenario: {description}\n\n"
                f"```{fence}\n{original_code}\n```"
            ),
        }],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    # Strip markdown fences if the model included them despite the system prompt.
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[: -3]
    return text.strip()


def create_scenario_variant(
    baseline_id: str,
    description: str,
    code: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """
    Run a scenario variant of `baseline_id`'s producing_code.

    - `description` is what the user typed ("what if...").
    - `code` lets callers (or tests) supply the modified code directly,
      bypassing the LLM rewrite.

    Returns the new figure entity record.
    """
    baseline = get_entity(baseline_id)
    if not baseline:
        raise ValueError(f"baseline {baseline_id} not found")
    if baseline["type"] != "figure":
        raise ValueError("scenarios can only be derived from figures for now")
    if not baseline.get("producing_code"):
        raise ValueError("baseline has no producing_code; can't derive a scenario")

    # Detect language from the baseline's producing_code (NOT from the agent's
    # `code` arg, since the agent may have submitted code that doesn't match
    # the baseline by accident). The agent's code SHOULD be in the baseline's
    # language; if it isn't, run_<lang> will surface a kernel error.
    language: Language = _detect_language(baseline["producing_code"])

    new_code = code or _rewrite_code_via_llm(
        baseline["producing_code"], description, language=language,
    )

    runner = "run_r" if language == "r" else "run_python"
    result_json = execute_tool(runner, {"code": new_code})
    result = json.loads(result_json)
    if result.get("error"):
        raise ValueError(f"scenario run failed ({runner}): {result['error']}")
    plots = result.get("plots") or []
    if not plots:
        stderr = result.get("stderr", "")[:300]
        raise ValueError(
            f"scenario produced no figures (ran via {runner})"
            + (f"; stderr: {stderr}" if stderr else "")
        )

    plot = plots[0]
    derived_title = title or _title_from_code(new_code) or f"{baseline['title']} ({description})"
    # Stage 2: scenarios carry the exec_id of the variant run too — they're
    # full figure entities and deserve the same drill-down to producing code
    # via the exec record. We take the first artifact (idx=0) since plots[0]
    # is what we're materializing as the entity.
    _exec_id_ptr = result.get("exec_id")
    eid = create_entity(
        entity_type="figure",
        title=derived_title[:120],
        artifact_path=plot["url"],
        producing_code=new_code,
        parent_entity_id=baseline["parent_entity_id"],
        scenario_of=baseline_id,
        metadata={
            "scenario_description": description,
            "original_name": plot.get("original_name", "figure.png"),
        },
        exec_id=_exec_id_ptr,
        artifact_kind="figure" if _exec_id_ptr else None,
        artifact_idx=0 if _exec_id_ptr else None,
    )
    add_edge(eid, baseline_id, "variantOf", {"description": description})
    return get_entity(eid)  # type: ignore[return-value]
