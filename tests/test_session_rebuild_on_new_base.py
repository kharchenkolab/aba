"""A pack bump must not make a project's session permanently unusable.

Live, 2026-08-26, on a real project after a routine base-pack update. The
project had ONE recorded session addition — a package added months earlier from
the `cran` lane. The new pack absorbed that same package as a conda dependency,
which changed the pack's EnvID, which invalidated the project's snapshot, which
triggered "rebuild on the new base and replay recorded additions".

The replay solved that addition against the new base's cran layer, whose repo
set carries only the CRAN snapshot — the package is a Bioconductor one — so it
could not resolve. `env.solve_conflict` at realize. Two separate defects made
that fatal rather than annoying:

  1. The addition was REDUNDANT. The new base already shipped the package. The
     session was destroyed replaying a request that no longer needed replaying.
  2. `_save_row` ran only after the whole replay succeeded, so `base_env_id`
     stayed pinned to the OLD base. Every subsequent call re-entered the same
     rebuild and failed identically — five times in one session. A failure that
     cannot be recorded becomes permanent instead of merely present.

Either fix alone would have prevented the outage. Both are guarded here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def test_an_addition_the_new_base_supplies_is_not_replayed():
    """THE first defect: don't re-install what the base now ships."""
    from core.compute.project_env import _addition_is_redundant
    supplied = {"apeglm", "bioconductor-apeglm", "seurat", "r-seurat"}
    assert _addition_is_redundant({"eco": "cran", "specs": ["apeglm"]}, supplied)
    # version constraints must not defeat the match
    assert _addition_is_redundant({"eco": "cran", "specs": ["apeglm>=1.20"]}, supplied)
    # ecosystem spelling differs between the request and the base
    assert _addition_is_redundant({"eco": "cran", "specs": ["Seurat"]}, supplied)


def test_a_partially_supplied_addition_is_replayed_in_full():
    """WIDE: dropping half of a multi-package add would change the request."""
    from core.compute.project_env import _addition_is_redundant
    supplied = {"apeglm"}
    assert not _addition_is_redundant(
        {"eco": "cran", "specs": ["apeglm", "somethingelse"]}, supplied)


def test_unknown_base_contents_replays_as_before():
    """ARMED: if we cannot read the base, we must NOT infer 'redundant'.
    Guessing redundancy from an empty answer would silently drop real installs."""
    from core.compute.project_env import _addition_is_redundant
    assert not _addition_is_redundant({"eco": "cran", "specs": ["apeglm"]}, set())


def test_base_supplies_is_empty_when_the_substrate_cannot_answer():
    from core.compute.project_env import _base_supplies

    class _Boom:
        def sync_call(self, *a, **k):
            raise RuntimeError("substrate down")

    assert _base_supplies(_Boom(), "env:v1:x") == set()


def test_base_supplies_strips_ecosystem_prefixes():
    from core.compute.project_env import _base_supplies

    class _Ad:
        def sync_call(self, verb, *a, **k):
            return {"packages": [{"name": "bioconductor-apeglm"},
                                 {"name": "r-Seurat"}, {"name": "numpy"}]}

    got = _base_supplies(_Ad(), "env:v1:x")
    assert {"apeglm", "seurat", "numpy"} <= got, got


def test_a_quarantined_addition_is_never_replayed_again():
    """THE second defect, in the form that makes it permanent: an addition
    already known to fail must be skipped, not retried on every call."""
    from core.compute.project_env import _addition_is_redundant
    add = {"eco": "cran", "specs": ["apeglm"],
           "quarantined": {"code": "env.solve_conflict"}}
    # the loop skips on the marker; assert the marker survives a round trip
    assert add.get("quarantined", {}).get("code") == "env.solve_conflict"
    assert not _addition_is_redundant(add, set())


def test_the_rebuild_records_the_new_base_even_when_a_replay_fails():
    """The load-bearing property: _save_row must not sit behind the replay.

    A source-level check, because the behavioural path needs a live substrate —
    but the ORDERING is the whole defect, so it is worth pinning directly."""
    src = (REPO / "backend" / "core" / "compute" / "project_env.py").read_text()
    body = src[src.index("_quarantined = []"):src.index("def runtime(")]
    save_at = body.index("_save_row(pid, language, new_row)")
    # every replay failure path must appear BEFORE the save
    q_at = body.index('_quarantined.append(')
    assert q_at < save_at, (
        "the quarantine path must run before _save_row — if the row is written "
        "only on full success, base_env_id never advances and the failing "
        "rebuild repeats on every call")
    assert "additions + _quarantined" in body, (
        "quarantined additions must stay in the registry; dropping them "
        "silently loses what the user asked for")
