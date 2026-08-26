"""Substrate refusals, rendered as levers an AGENT can actually pull.

The substrate upgraded under us on 2026-08-25 (a floating WEFT_REF), bringing
two refusals that did not exist when this mapping was written:

  env.post_link_scripts   a conda post-link script was staged and never run, so
                          the package would install with its payload missing.
                          Refusing is right — that is the DESeq2 failure, caught
                          at build instead of three steps later.
  env.activation_failed   the env did not activate on the node; the user command
                          never ran.

`_typed_task_error` renders the substrate's own hints, which is usually the best
available text. It is the wrong text here: both post-link levers are deployment
edits — a pinned `post_install` in the env pack, or a site policy change — and
an agent can make neither. An agent handed a fix it cannot perform either loops
on it or invents a substitute. `_ABA_LEVERS` exists for exactly this case: a
code whose upstream hint names a verb the caller cannot reach.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def _render(code: str, detail: str = "d", hints=None) -> str:
    from core.jobs.weft_submitter import _typed_task_error
    return _typed_task_error({"error": code, "detail": detail,
                              "hints": hints or {}}) or ""


def test_post_link_refusal_names_a_lever_the_agent_has():
    msg = _render("env.post_link_scripts")
    assert msg, "the code must render"
    low = msg.lower()
    assert "post-link" in low or "post_link" in low, msg
    assert "operator" in low, "the agent must be told whose fix this is"
    assert "different package" in low or "alternative" in low, msg


def test_activation_failure_is_not_blamed_on_the_analysis():
    """The worst outcome here is an agent rewriting working code to chase an
    infrastructure fault."""
    msg = _render("env.activation_failed").lower()
    assert "not your code" in msg or "infrastructure" in msg, msg


def test_a_cran_name_miss_is_not_answered_with_another_env():
    """`env.solve_conflict` carries TWO different failures. For a genuine pin
    conflict "build an isolated env" is right. For a name the repository set
    does not carry it is actively harmful: the new env solves against the same
    repos and fails identically, so the agent retries and mints another. That
    is the retry-by-new-env mechanism — one live project accumulated four envs
    for one library and ~3.3 GB before anyone looked."""
    msg = _render("env.solve_conflict",
                  hints={"ecosystem": "cran", "missing": ["DESeq2"],
                         "snapshot": "2026-08-05"})
    low = msg.lower()
    assert "deseq2" in low, "the lever must name the package that was missed"
    assert "not build another" in low or "do not build" in low, msg
    assert "bioconductor-" in low and "r-<name>" in low, (
        "the reachable lever is the conda spelling")


def test_a_real_pin_conflict_still_gets_the_isolated_env_lever():
    """WIDE: the other side of the same code. An ecosystem-less conflict, and
    a cran conflict with no missing names, both keep the original advice."""
    for hints in ({}, {"ecosystem": "cran"}, {"missing": []},
                  {"ecosystem": "conda", "missing": ["numpy"]}):
        msg = _render("env.solve_conflict", hints=hints).lower()
        assert "make_isolated_env" in msg, (hints, msg)


def test_a_lever_that_raises_never_eats_the_error(monkeypatch):
    """DEGENERATE: a hint-aware lever is code, and code can throw. The
    substrate's diagnosis must still reach the agent."""
    from core.jobs import weft_submitter as ws

    def _boom(hints):
        raise KeyError("bad lever")
    monkeypatch.setitem(ws._ABA_LEVERS, "env.solve_conflict", _boom)
    msg = _render("env.solve_conflict", detail="the substrate said this")
    assert "the substrate said this" in msg, msg


def test_unmapped_codes_still_use_the_substrate_hint():
    """WIDE: the map stays small. A code we have NOT thought about keeps the
    substrate's own (usually good) wording rather than a generic."""
    msg = _render("env.something_new", detail="the substrate said this")
    assert "the substrate said this" in msg, msg


def test_the_codes_we_map_are_codes_the_substrate_actually_emits():
    """A lever for a code that does not exist is dead text that reads as
    coverage. Check against the substrate's own error table when a checkout is
    reachable."""
    sys.path.insert(0, str(REPO / "tests"))
    from _weft_checkout import find_weft_file, tried
    _rel = ("src", "weft", "errors.py")
    src = find_weft_file(*_rel)
    if src is None:
        pytest.skip("no weft checkout: looked in " + tried(*_rel))
    text = src.read_text()
    from core.jobs.weft_submitter import _ABA_LEVERS
    missing = [c for c in _ABA_LEVERS if f'"{c}"' not in text]
    assert not missing, (
        f"these codes have aba levers but the substrate does not define them: "
        f"{missing} — either the substrate renamed them (the lever is now dead "
        f"text) or they were never real")
