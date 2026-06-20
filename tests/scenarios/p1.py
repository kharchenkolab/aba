"""Phase 1 — baseline tool-call mechanics.

8 single-tool scenarios across diverse tool families. Each prompt is
deliberately direct so a competent model has only one reasonable
move. No recipes, no skill discovery, no multi-turn — just "the model
can pick the right tool from a 60-tool catalog and pass correct
args". If P1 doesn't pass cleanly the rest of the phases are moot.

Assertions are strict on shape (first tool + arg validity) but
permissive on content (we don't pin the exact arg phrasing where the
user prompt invites paraphrase — e.g. memory note bodies).
"""
from __future__ import annotations

from tests.scenarios import Scenario, Assertion


# ── Per-scenario assertion helpers ─────────────────────────────────


def _first_tool_is(expected: str | tuple[str, ...]):
    targets = (expected,) if isinstance(expected, str) else expected

    def _p(calls):
        if not calls:
            return False, "no tools were called"
        n = calls[0][0]
        return (n in targets,
                f"first tool was {n!r}, expected one of {targets}")
    return _p


def _arg_equals(field: str, expected: str):
    def _p(calls):
        if not calls:
            return False, "no tools"
        args = calls[0][1]
        val = args.get(field)
        return (val == expected,
                f"{field}={val!r}, expected {expected!r}")
    return _p


def _arg_contains(field: str, substr: str):
    def _p(calls):
        if not calls:
            return False, "no tools"
        val = str(calls[0][1].get(field) or "")
        return (substr.lower() in val.lower(),
                f"{field}={val[:80]!r} doesn't contain {substr!r}")
    return _p


def _no_extra_optional_args(allowed: set[str]):
    """Catches the over-helpful-LLM pattern of adding optional args
    that narrow the result. e.g. search_pypi(query='X', filter='Y')
    when only `query` was needed."""
    def _p(calls):
        if not calls:
            return False, "no tools"
        args = calls[0][1] or {}
        extras = [k for k in args if k not in allowed
                  and args.get(k) not in (None, "", [], {})]
        return (not extras,
                f"first call passed unexpected non-empty args: {extras}")
    return _p


def _exactly_one_tool_call(calls):
    return (len(calls) == 1,
            f"{len(calls)} tools called, expected exactly 1")


# ── Scenarios ──────────────────────────────────────────────────────


P1_SCENARIOS: list[Scenario] = [
    Scenario(
        name="p1_list_data_files",
        user_prompt="list the data files in this project",
        assertions=[
            Assertion("first_tool_is_list_data_files",
                      _first_tool_is("list_data_files")),
            Assertion("no_extra_optional_args",
                      _no_extra_optional_args(set())),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_write_memory",
        user_prompt=("save a memory note titled 'p1-test' with body "
                     "'hello from phase one'"),
        assertions=[
            Assertion("first_tool_is_write_memory",
                      _first_tool_is("write_memory")),
            Assertion("title_is_p1_test",
                      _arg_equals("name", "p1-test")),
            Assertion("body_contains_phrase",
                      _arg_contains("body", "phase one")),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_read_memory",
        user_prompt="read my memory note named 'p1-test'",
        assertions=[
            Assertion("first_tool_is_read_memory",
                      _first_tool_is("read_memory")),
            Assertion("name_is_p1_test",
                      _arg_equals("name", "p1-test")),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_search_pypi",
        user_prompt="search PyPI for the 'pandas' package",
        assertions=[
            Assertion("first_tool_is_search_pypi",
                      _first_tool_is("search_pypi")),
            Assertion("query_is_pandas",
                      _arg_contains("query", "pandas")),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_list_entities_datasets",
        user_prompt="show me the datasets registered in this project",
        assertions=[
            # list_entities OR list_data_files are both reasonable.
            Assertion("first_tool_is_listing",
                      _first_tool_is(("list_entities",
                                      "list_data_files"))),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_list_capabilities",
        user_prompt="what capabilities are installed?",
        assertions=[
            Assertion("first_tool_is_list_capabilities",
                      _first_tool_is("list_capabilities")),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_find_files_csv",
        user_prompt=("find any files matching the pattern '*.csv' "
                     "in this project"),
        assertions=[
            Assertion("first_tool_is_find_files",
                      _first_tool_is("find_files")),
            Assertion("pattern_is_csv",
                      _arg_contains("pattern", "*.csv")),
        ],
        max_turns=2,
    ),
    Scenario(
        name="p1_ensure_capability_pandas",
        user_prompt=("make sure pandas is installed and ready to use"),
        assertions=[
            Assertion("first_tool_is_ensure_capability",
                      _first_tool_is("ensure_capability")),
            Assertion("name_is_pandas",
                      _arg_contains("name", "pandas")),
        ],
        max_turns=2,
    ),
]
