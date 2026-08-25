"""A scenario must be able to assert what a turn COST, not only what it achieved.

Live, 2026-08-25. A user asked for an R library that the deployment's mounted
base pack already contains and verifies at build time. Nothing in the platform
could recognize it as provided, so the request fell through to an external
registry and built a 2.0 GB duplicate environment beside the mounted one, over
roughly fifteen minutes.

Every assertion in the suite was satisfiable by that outcome. `must_mention`
passes on prose. `tools_used` passes on the tool being invoked. `produces`
passes on artifacts. `background_job.ok` passes on a job that ran clean. The
entire `expect:` vocabulary described ACHIEVEMENT, so the suite could reward
doing more but never penalise doing far too much.

The schema already contained the insight, applied to exactly one thing:

    `entities_of_type` alone can only reward creating more, so pair it with a
    ceiling. The ceiling is what makes such a guard two-sided.

That is a general rule about work, and it had been applied only to entity
counts. These two checks apply it to environments and to wall clock.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "regtest" / "harness"))


class _Client:
    def get(self, path, **kw):
        return type("R", (), {"json": lambda _s: {}})()


def _fails(exp, cap_extra):
    import runner
    step = {"expect": exp}
    cap = {"text": "", "tools": [], "tool_calls": [], "jobs": [], **cap_extra}
    return runner.run_checks(step, cap, {}, [], _Client(), "p", "t", {}, [])


def test_env_ceiling_catches_a_redundant_build():
    """THE regression: the step created an env it did not need to."""
    f = _fails({"envs_created_max": 0}, {"envs_before": 0, "envs_after": 1})
    assert any("envs_created_max" in x for x in f), f


def test_env_ceiling_passes_when_nothing_was_built():
    """WIDE: answering from the base pack is the behaviour we want."""
    f = _fails({"envs_created_max": 0}, {"envs_before": 2, "envs_after": 2})
    assert not any("envs_created_max" in x for x in f), f


def test_env_ceiling_allows_a_deliberate_build():
    """WIDE: a scenario that MEANS to build an isolated env says so."""
    f = _fails({"envs_created_max": 1}, {"envs_before": 0, "envs_after": 1})
    assert not any("envs_created_max" in x for x in f), f


def test_env_ceiling_is_armed_against_no_measurement():
    """An unmeasured ceiling must FAIL. A substrate that could not be counted
    reads as 'created nothing' to any check that treats None as zero — the
    failure mode that turns a ceiling into decoration."""
    f = _fails({"envs_created_max": 0}, {"envs_before": None, "envs_after": None})
    assert any("NOT MEASURED" in x for x in f), f


def test_wall_ceiling_catches_a_slow_step():
    f = _fails({"step_seconds_max": 30}, {"elapsed_s": 900.0})
    assert any("step_seconds_max" in x for x in f), f


def test_wall_ceiling_passes_a_fast_step():
    f = _fails({"step_seconds_max": 30}, {"elapsed_s": 4.2})
    assert not any("step_seconds_max" in x for x in f), f


def test_wall_ceiling_is_armed_against_no_measurement():
    f = _fails({"step_seconds_max": 30}, {})
    assert any("NOT MEASURED" in x for x in f), f


def test_ceilings_are_opt_in():
    """WIDE: scenarios that declare neither are untouched."""
    f = _fails({}, {"envs_before": 0, "envs_after": 5, "elapsed_s": 9999.0})
    assert not any("envs_created_max" in x or "step_seconds_max" in x for x in f), f
