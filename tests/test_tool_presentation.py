"""Tool-presentation policy (core.runtime.mcp.presentation) — the single source of
truth for per-mode catalog rendering.

The invariant these lock in: the calling CONTRACT (property names, types, required,
enum, default) is IDENTICAL across every prompt_mode; only PROSE (docstrings, param
descriptions, titles) is tiered. This is the guard that stops a budget tweak for one
tier (lean) from silently reshaping another (standard/production). See
misc/tool_presentation.md.
"""
from __future__ import annotations
import copy
import os
import sys

_BE = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BE not in sys.path:
    sys.path.insert(0, _BE)

from core.runtime.mcp.presentation import (  # noqa: E402
    ToolPresentation, presentation_for, strip_schema_prose)


def test_policy_per_mode():
    assert presentation_for("full") == ToolPresentation("full", "keep")
    assert presentation_for("standard") == ToolPresentation("summary", "keep")
    assert presentation_for("lean") == ToolPresentation("summary", "drop")
    assert presentation_for("lean_small") == ToolPresentation("summary", "drop")


def test_unknown_mode_defaults_to_full():
    assert presentation_for(None) == ToolPresentation("full", "keep")
    assert presentation_for("bogus") == ToolPresentation("full", "keep")


def test_standard_never_drops_param_prose():
    """The production tier (grounded_guide) must keep full param prose — it is not
    window-bound and must never be pressured to drop it to satisfy a lean budget."""
    assert presentation_for("standard").param_prose == "keep"
    assert presentation_for("full").param_prose == "keep"


_SCHEMA = {
    "type": "object",
    "title": "run_pythonArguments",
    "properties": {
        "code": {"type": "string", "title": "Code", "description": "the code to run"},
        "est_gpu": {"type": "boolean", "default": False, "title": "Est Gpu",
                    "description": "set True for a GPU workload"},
        # a parameter literally NAMED "title" — must survive prose stripping (regression:
        # run_python has this param; a naive strip dropped it, breaking the contract).
        "title": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None,
                  "title": "Title", "description": "job title"},
        "execution": {"anyOf": [{"type": "string", "enum": ["slurm", "local", "auto"]},
                                {"type": "null"}],
                      "default": None, "title": "Execution", "description": "where it runs"},
    },
    "required": ["code"],
}


def test_strip_removes_prose_but_preserves_contract():
    out = strip_schema_prose(_SCHEMA)
    # prose gone (top-level + every property's OWN metadata, recursively)
    assert "description" not in out
    for p in out["properties"].values():
        assert "description" not in p and "title" not in p
    # CONTRACT intact — every property NAME preserved (incl. the one named "title")
    assert set(out["properties"]) == {"code", "est_gpu", "title", "execution"}, \
        "a property named 'title' must NOT be stripped as if it were metadata"
    assert out["type"] == "object"
    assert out["properties"]["code"]["type"] == "string"
    assert out["properties"]["est_gpu"]["default"] is False
    assert out["required"] == ["code"]
    anyof = out["properties"]["execution"]["anyOf"]
    assert {"type": "string", "enum": ["slurm", "local", "auto"]} in anyof


def test_strip_is_nondestructive():
    orig = copy.deepcopy(_SCHEMA)
    _ = strip_schema_prose(_SCHEMA)
    assert _SCHEMA == orig, "strip_schema_prose must not mutate its input"
