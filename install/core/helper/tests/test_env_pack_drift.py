"""A deployment whose env packs have fallen behind must be TOLD.

`$ABA_HOME/installation/envs/` belongs to the operator: the installer writes it
once, on a fresh install, and `aba update` never overwrites it. That is the
right policy — a deployment's environment is not ours to change under it — but
it has a silent failure mode. A pin bump in the repo reaches an existing
deployment through nothing whatsoever, and from inside that deployment a pack
that is three releases behind looks exactly like one that is current.

That stopped being cosmetic when lstar 0.2.2 made a store's counts basis a hard
requirement: a deployment still on 0.2.1 keeps writing stores the pinned viewer
refuses, and nothing in the install says why. Measured on the dev box while
adding this: the deployed pack had picked up an earlier `pyarrow` change by
hand, so it was neither current nor a clean copy — drift in both directions,
with no way to see it.

doctor reports the difference and prints the fix. It must never rewrite the
operator's file.
"""
from pathlib import Path

import pytest

from aba_installer.cli import env_pack_drift, pack_pins


def _home(tmp_path: Path, shipped: str, deployed: "str | None",
          name: str = "python_bio.yaml") -> Path:
    s = tmp_path / "repo" / "aba" / "install" / "core" / "envs"
    d = tmp_path / "installation" / "envs"
    s.mkdir(parents=True, exist_ok=True)
    d.mkdir(parents=True, exist_ok=True)
    (s / name).write_text(shipped)
    if deployed is not None:
        (d / name).write_text(deployed)
    return tmp_path


PINNED = "spec:\n  deps:\n    pypi:\n      - lstar-sc ==0.2.2\n      - \"zarr >=3.1\"\n"
BEHIND = "spec:\n  deps:\n    pypi:\n      - lstar-sc ==0.2.1\n      - \"zarr >=3.1\"\n"


# ── ARMED: a scanner that reads nothing reports every deployment current ──────

def test_the_scanner_reads_real_pins():
    pins = pack_pins(PINNED)
    assert pins == {"lstar-sc": "0.2.2"}, pins


def test_a_FLOOR_is_not_a_pin_and_is_not_drift():
    """`zarr >=3.1` has no version to compare; treating it as one would report
    permanent drift and train everyone to ignore this check."""
    assert "zarr" not in pack_pins(PINNED)


def test_a_commented_pin_is_not_read():
    assert pack_pins("      # - lstar-sc ==0.1.0\n      - lstar-sc ==0.2.2\n") \
        == {"lstar-sc": "0.2.2"}


# ── the report ───────────────────────────────────────────────────────────────

def test_THE_CASE_a_deployment_left_behind_is_reported(tmp_path):
    """The live shape: the repo moved to 0.2.2, the deployment is still on
    0.2.1, and nothing anywhere says so."""
    got = env_pack_drift(_home(tmp_path, PINNED, BEHIND))
    assert got == [("python_bio.yaml", "lstar-sc", "0.2.1", "0.2.2")], got


def test_a_matching_deployment_is_SILENT(tmp_path):
    """CEILING. A check that fires on a healthy install is noise, and noise is
    how a real drift report gets ignored."""
    assert env_pack_drift(_home(tmp_path, PINNED, PINNED)) == []


def test_a_deployment_AHEAD_of_the_repo_is_also_drift(tmp_path):
    """Direction-agnostic on purpose: an operator who pinned forward by hand is
    running something this ABA was never tested against. Report it and let them
    decide — the fix text says "or keep yours deliberately"."""
    got = env_pack_drift(_home(tmp_path, BEHIND, PINNED))
    assert got == [("python_bio.yaml", "lstar-sc", "0.2.2", "0.2.1")], got


def test_a_dep_pinned_on_only_ONE_side_is_drift(tmp_path):
    """WIDE: an added or removed pin is drift too. Comparing only the deps that
    appear in both would miss a dep the deployment never got."""
    got = env_pack_drift(_home(tmp_path, PINNED,
                               "spec:\n  deps:\n    pypi:\n      - \"zarr >=3.1\"\n"))
    assert got == [("python_bio.yaml", "lstar-sc", None, "0.2.2")], got


def test_an_UNPINNED_deployed_dep_is_drift(tmp_path):
    """The bare-name shape — it solves fine and bakes whatever the repo serves,
    which is exactly the version-skew this whole exercise was about."""
    got = env_pack_drift(_home(tmp_path, PINNED,
                               "spec:\n  deps:\n    pypi:\n      - lstar-sc\n"))
    assert got == [("python_bio.yaml", "lstar-sc", None, "0.2.2")], got


# ── degenerate shapes: doctor must not crash on a half-built install ──────────

def test_a_pack_the_deployment_never_took_is_not_drift(tmp_path):
    """A pack absent from installation/envs was never adopted (r_bio on a
    python-only box). Reporting it would demand a pack nobody asked for."""
    assert env_pack_drift(_home(tmp_path, PINNED, None)) == []


def test_a_missing_repo_or_installation_dir_reports_nothing(tmp_path):
    """doctor runs on broken installs by definition — this check must degrade to
    silence, never to a traceback that hides every check after it."""
    assert env_pack_drift(tmp_path) == []
    (tmp_path / "installation" / "envs").mkdir(parents=True)
    assert env_pack_drift(tmp_path) == []


def test_an_unreadable_pack_is_skipped_not_fatal(tmp_path):
    home = _home(tmp_path, PINNED, BEHIND)
    bad = home / "installation" / "envs" / "r_bio.yaml"
    (home / "repo" / "aba" / "install" / "core" / "envs" / "r_bio.yaml").write_text(PINNED)
    bad.mkdir()                       # a directory where a file should be
    got = env_pack_drift(home)
    assert ("python_bio.yaml", "lstar-sc", "0.2.1", "0.2.2") in got


def test_the_report_never_writes(tmp_path):
    """ACTION, not output. The operator owns installation/envs — the whole
    reason this is a report and not a sync."""
    home = _home(tmp_path, PINNED, BEHIND)
    dep = home / "installation" / "envs" / "python_bio.yaml"
    before = dep.read_bytes()
    env_pack_drift(home)
    assert dep.read_bytes() == before


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
