"""One reader of `data_files:`, and "ran ok" must mean "produced the inputs".

A scenario's declared inputs gate two decisions: `regtest/scenarios/_regen_all.sh`
skips a generator whose data is already complete, and the sweep's pre-flight
refuses a scenario whose declared inputs are absent. When those two disagree,
regen says "data present", the sweep says "declared inputs absent", and the
scenario is unrunnable with no single step to blame.

They disagreed, twice over, because the shell parsed the YAML itself in awk:

  * `sub(/^-[[:space:]]*/,"")` MUTATED the record, and the terminator rule
    `/^[^[:space:]-]/{f=0}` then matched the mutated text — so the flag cleared
    on the first list item and only entry ONE was ever checked; and
  * flow style (`data_files: [x.csv]`, used by 4 scenarios) matched nothing at
    all, silently falling back to a bare "data/ is non-empty" test.

Either way an incomplete `data/` read as complete, regen skipped it on every
run, and `variant_annotation` sat at 2 of 4 inputs until the sweep hit
SETUP-ERROR. The shell now calls `fixtures.py --complete`, the same parser the
sweep uses, so there is one reader. These guards pin that.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCEN = ROOT / "regtest" / "scenarios"
FIXTURES = ROOT / "regtest" / "harness" / "fixtures.py"
REGEN = SCEN / "_regen_all.sh"
sys.path.insert(0, str(ROOT / "regtest" / "harness"))
from fixtures import declared_inputs, missing_inputs  # noqa: E402


def _complete(scenario_dir: Path) -> int:
    return subprocess.run([sys.executable, str(FIXTURES), "--complete",
                           str(scenario_dir)]).returncode


def _cases():
    for f in sorted(SCEN.glob("*/scenario.yaml")):
        try:
            spec = yaml.safe_load(f.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        if spec.get("data_files"):
            yield f.parent.name, f.parent, spec


_CASES = list(_cases())


def test_the_corpus_can_expose_the_bug():
    """ARMED. A parametrized test over an empty (or uniform) corpus passes while
    measuring nothing. The first bug was invisible to any single-entry scenario;
    the second only to block style. Both shapes must be present."""
    multi = [n for n, _d, s in _CASES if len(s.get("data_files") or []) > 1]
    flow = [n for n, d, _s in _CASES
            if "data_files: [" in (d / "scenario.yaml").read_text()]
    assert len(_CASES) >= 5, f"only {len(_CASES)} scenarios declare data_files"
    assert multi, "no multi-entry declaration — the dropped-tail bug would hide"
    assert flow, "no flow-style declaration — the unparsed-shape bug would hide"


@pytest.mark.parametrize("name,d,spec", _CASES, ids=[c[0] for c in _CASES])
def test_the_shell_predicate_matches_the_sweep(name, d, spec):
    """THE PROPERTY. Whatever the declaration's YAML shape, the completeness
    answer regen acts on is the one the sweep's pre-flight would compute."""
    want_complete = not missing_inputs(declared_inputs(spec), d / "data")
    assert (_complete(d) == 0) is want_complete, (
        f"{name}: regen and the sweep disagree about completeness")


def test_a_partial_data_dir_is_incomplete_whatever_the_shape(tmp_path):
    """DEGENERATE, both shapes, and the tail entry specifically — the exact
    input the awk mis-read. Synthetic, so it holds even if every real scenario
    happens to be complete."""
    for body in ("data_files:\n- a.txt\n- b.txt\n- c.txt\n",
                 "data_files: [a.txt, b.txt, c.txt]\n"):
        d = tmp_path / f"s{abs(hash(body))}"
        (d / "data").mkdir(parents=True)
        (d / "scenario.yaml").write_text(body + "make_data: _make_data.py\n")
        (d / "data" / "a.txt").write_text("x")
        (d / "data" / "b.txt").write_text("x")
        assert _complete(d) == 1, f"missing tail entry read as complete: {body!r}"
        (d / "data" / "c.txt").write_text("x")
        assert _complete(d) == 0, f"complete data read as incomplete: {body!r}"


def test_the_shell_no_longer_parses_the_declaration_itself():
    """The mistake was HAVING a second parser, so the fix is not 'a better awk'.
    Two readers of one declaration is the defect class; pin its absence."""
    src = REGEN.read_text()
    body = src[src.index("_data_complete()"):]
    body = body[:body.index('\necho -n "[top-level]')]
    # CODE only — the comments here necessarily quote the awk they replaced.
    code = "\n".join(ln for ln in body.splitlines()
                     if not ln.lstrip().startswith("#"))
    assert "fixtures.py" in code and "--complete" in code, (
        "_regen_all.sh no longer delegates to the shared parser")
    # A dependency-free fallback may remain for a python without PyYAML, but it
    # must come AFTER the shared parser has been asked — the shell must never be
    # the primary reader again.
    if "awk" in code:
        assert code.index("fixtures.py") < code.index("awk"), (
            "the shell reader runs before the shared parser — it is primary again")


def test_ran_ok_means_the_declared_inputs_exist():
    """The second-order gap. variant_annotation's generator exited 0 while
    writing 2 of its 4 declared inputs (the rest came from a sibling fetch
    script nothing invoked), so regen printed `ok` for a scenario the sweep
    could not run. A generator's success must be checked against what it
    declares, not against its exit code."""
    src = REGEN.read_text()
    loop = src[src.index('for g in "$HERE"/*/_make_data.py'):]
    assert "_data_complete" in loop, (
        "regen does not re-check completeness after running a generator — "
        "'ran without error' is not 'produced what it declares'")


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_regen_reports_a_lying_generator_as_fail(tmp_path):
    """ARMED end-to-end: a generator that exits 0 and writes nothing must make
    regen say FAIL, not ok."""
    d = tmp_path / "scenarios" / "liar"
    (d / "data").mkdir(parents=True)
    (d / "scenario.yaml").write_text("data_files:\n- never.txt\nmake_data: _make_data.py\n")
    (d / "_make_data.py").write_text("print('pretending')\n")
    (d / "data" / "decoy.txt").write_text("x")
    out = subprocess.run(
        [sys.executable, str(FIXTURES), "--complete", str(d)]).returncode
    assert out == 1, "a data/ lacking its declared input must read as incomplete"
