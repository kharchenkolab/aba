"""regtest sweep — declared scenario preconditions (`requires:`) and their
three-way infra classification.

Two defects, one instrument (regtest/harness/preconditions.py):

1. COVERAGE WAS ZERO AND THE SWEEP SAID GREEN. `runner.main` declines a
   `requires: slurm` scenario unless the resolved submitter is slurm (exit 4).
   The sweep never exported ABA_BATCH_SUBMITTER, so on a box WITH working Slurm
   all three scheduler scenarios declined — every sweep, including the one whose
   baseline was accepted, which is why their reference is itself an error. The
   sweep now PROVIDES a declared requirement, and pre-flight REFUSES a selection
   whose requirement this host cannot honour: a run that measures nothing must
   fail, not pass quietly.

2. A DECLINE WAS REPORTED AS A CREDENTIAL FAILURE. The end-of-run banner
   announced those three as "CREDENTIAL/RATE-LIMIT errors … re-run under fresh
   creds". Nothing was wrong with the credentials; the remedy was the
   precondition. Declined is now its own class, carrying its real reason.

The load-bearing assertions here are on the ACTION — the env handed to the
runner subprocess, whether run_scenario was called at all, and which bucket a
row lands in — not on the text of a banner.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "regtest" / "harness"))

_spec = importlib.util.spec_from_file_location(
    "aba_sweep_req", ROOT / "regtest" / "harness" / "sweep.py")
sweep = importlib.util.module_from_spec(_spec)
sys.modules["aba_sweep_req"] = sweep
_spec.loader.exec_module(sweep)

import preconditions as pre  # noqa: E402 — needs the sys.path above

pytestmark = pytest.mark.platform


# ---------- helpers ----------

def _home(tmp_path, *, slurm_site=True, name="cluster", body=None) -> Path:
    """An eval home whose weft-sites.yaml declares (or does not declare) a
    slurm-kind site — the thing that decides whether ABA_BATCH_SUBMITTER=slurm
    reaches the cluster or degrades to the local lane."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    if body is not None:
        (home / "weft-sites.yaml").write_text(body)
    elif slurm_site:
        (home / "weft-sites.yaml").write_text(
            f"sites:\n  - name: {name}\n    kind: slurm\n    config: {{}}\n")
    return home


def _ok_probes(calls=None):
    """A host that has Slurm and answers. `calls` records probe use so a test
    can assert on what was CONSULTED, not only on what was returned."""
    def which(b):
        (calls if calls is not None else []).append(("which", b))
        return f"/usr/bin/{b}"

    def ping():
        (calls if calls is not None else []).append(("ping", None))
        return True
    return {"which": which, "ping": ping}


def _scen_tree(tmp_path, specs: dict) -> Path:
    """A scenarios/ tree: {sid: yaml-body}."""
    scen = tmp_path / "scenarios"
    for sid, body in specs.items():
        (scen / sid).mkdir(parents=True)
        (scen / sid / "scenario.yaml").write_text(body)
    return scen


# ---------- the probe: WIDE over the shapes a host can take ----------

def test_probe_passes_on_a_host_that_has_slurm(tmp_path):
    assert pre.slurm_problems(_home(tmp_path), **_ok_probes()) == []


def test_probe_fails_when_no_slurm_client_is_installed(tmp_path):
    calls = []
    p = _ok_probes(calls)
    problems = pre.slurm_problems(_home(tmp_path), which=lambda b: None,
                                  ping=p["ping"])
    assert problems and "PATH" in problems[0]
    assert ("ping", None) not in calls, \
        "pinged a controller with no client installed — the probe must short-circuit"


def test_probe_fails_when_the_controller_does_not_answer(tmp_path):
    p = _ok_probes()
    problems = pre.slurm_problems(_home(tmp_path), which=p["which"],
                                  ping=lambda: False)
    assert problems and "ping" in problems[0]


def test_probe_fails_when_no_slurm_site_is_declared(tmp_path):
    """The 'fake more permissive than reality' shape, and the reason the probe
    is not just `which sbatch`: with no slurm-kind weft site declared,
    core/jobs/submitter.py::_slurm_lane hands the job to the LOCAL lane while
    submitter_name() still says 'slurm'. The runner's precondition passes, the
    scenario runs, and its row claims scheduler coverage for a local job."""
    for body in (None,                                   # no file at all
                 "sites: []\n",                          # declared nothing
                 "sites:\n  - name: n\n    kind: ssh\n",  # nothing SLURM-kind
                 "sites: [oops\n"):                      # unparseable
        home = _home(tmp_path / f"h{abs(hash(str(body)))}", slurm_site=False,
                     body=body)
        problems = pre.slurm_problems(home, **_ok_probes())
        assert any("weft site" in p for p in problems), (body, problems)
    assert pre.declared_slurm_sites(_home(tmp_path, name="c2")) == ["c2"]


def test_requirement_normalization_and_env(tmp_path):
    """The declaration is normalized exactly as the runner normalizes it, and
    the env is keyed on that value — never on a scenario id."""
    assert pre.requirement_of({"requires": " Slurm "}) == "slurm"
    assert pre.requirement_of({"requires": None}) == ""
    assert pre.requirement_of({}) == ""
    assert pre.requirement_env("slurm") == {"ABA_BATCH_SUBMITTER": "slurm"}
    assert pre.requirement_env("") == {}
    assert pre.requirement_env("nonesuch") == {}
    # and the mapping is not shared state a caller can mutate
    pre.requirement_env("slurm")["ABA_BATCH_SUBMITTER"] = "local"
    assert pre.requirement_env("slurm") == {"ABA_BATCH_SUBMITTER": "slurm"}


def test_unknown_requirement_is_a_problem_not_a_no_op(tmp_path):
    """A typo'd or newly-invented `requires:` value is honoured by nobody: the
    runner only knows 'slurm', so the scenario runs as if it declared nothing.
    A requirement nothing can satisfy must not read as no requirement."""
    scen = _scen_tree(tmp_path, {"typo": "requires: slrum\nsteps: [a]\n"})
    res = pre.check_requirements(scen, ["typo"], _home(tmp_path), **_ok_probes())
    assert res["examined"] == 1
    assert "slrum" in res["problems"]
    assert "unknown requirement" in res["problems"]["slrum"][0]


def test_check_requirements_says_when_it_examined_nothing(tmp_path):
    """ARMED, degenerate: a selection with no `requires:` at all makes this
    check vacuous — and vacuous must be visible, not a clean bill."""
    scen = _scen_tree(tmp_path, {"plain": "steps: [a]\n"})
    res = pre.check_requirements(scen, ["plain"], _home(tmp_path), **_ok_probes())
    assert res == {"requiring": {}, "examined": 0, "problems": {}}


# ---------- the seam: the env we export is the one the platform reads ----------

def test_the_provided_env_actually_selects_the_slurm_submitter(monkeypatch):
    """The load-bearing property, asserted against the PLATFORM's selector
    rather than a string. `runner.main` declines unless
    core.jobs.submitter.submitter_name() == 'slurm'; that function reads the
    registry setting backed by ABA_BATCH_SUBMITTER. If either side moves, the
    sweep goes back to exporting something nobody reads."""
    from core.jobs.submitter import submitter_name
    monkeypatch.delenv("ABA_BATCH_SUBMITTER", raising=False)
    assert submitter_name() != "slurm", "unprovided host already reads as slurm"
    for k, v in pre.requirement_env("slurm").items():
        monkeypatch.setenv(k, v)
    assert submitter_name() == "slurm", (
        "the env the sweep exports does not select the submitter the runner's "
        "precondition checks")


def test_sites_config_path_is_in_lockstep_with_the_platform(monkeypatch, tmp_path):
    """The declared-site probe reads a file the PLATFORM owns the location of
    (core.compute.adapter.sites_config_path → $ABA_HOME/weft-sites.yaml). Two
    readers of one fact is a property, not a comment: if the platform moves the
    file, this pre-flight would silently report 'no slurm site' forever."""
    from core.compute.adapter import sites_config_path
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    assert Path(sites_config_path()) == pre.sites_config_path_for(tmp_path)


# ---------- sweep wiring: the requirement reaches the runner subprocess ----------

def _capture_runner_env(monkeypatch, tmp_path, sid, body):
    """Run sweep.run_scenario against a stubbed subprocess and return the env
    it would have handed the runner."""
    monkeypatch.setattr(sweep, "SCEN", _scen_tree(tmp_path, {sid: body}))
    monkeypatch.setattr(sweep, "RUNS", tmp_path / "runs")
    seen = {}

    class _R:
        returncode = 4                      # short-circuit before report.json

    def _fake_run(*a, **k):
        seen.update(k.get("env") or {})
        return _R()

    monkeypatch.setattr(sweep.subprocess, "run", _fake_run)
    sweep.run_scenario(sid, "haiku")
    return seen


def test_runner_env_carries_the_requirement(monkeypatch, tmp_path):
    """The fix itself, asserted on the ACTION (the env handed to the child),
    not on a banner: a `requires: slurm` scenario is launched with the submitter
    its precondition demands."""
    env = _capture_runner_env(monkeypatch, tmp_path, "needs_slurm",
                              "requires: slurm\nsteps: [a]\n")
    assert env.get("ABA_BATCH_SUBMITTER") == "slurm"


def test_a_scenario_without_the_declaration_is_not_re_placed(monkeypatch, tmp_path):
    """The other side, and the blast-radius bound: providing the requirement
    must stay scoped to the scenarios that DECLARE it. Forcing the cluster lane
    on all 38 would re-place every background job in the sweep and re-baseline
    the whole instrument."""
    monkeypatch.delenv("ABA_BATCH_SUBMITTER", raising=False)
    env = _capture_runner_env(monkeypatch, tmp_path, "plain", "steps: [a]\n")
    assert "ABA_BATCH_SUBMITTER" not in env


def test_the_real_tree_scenarios_get_their_submitter():
    """…and on the REAL scenarios/ tree, so the guard cannot pass on fixtures
    while the shipped scenarios stay uncovered. ARMED: if nothing in the tree
    declares a requirement, this measured nothing and says so."""
    sids = sorted(p.parent.name for p in sweep.SCEN.glob("*/scenario.yaml"))
    reqs = pre.scenario_requirements(sweep.SCEN, sids)
    assert reqs, "no scenario declares `requires:` — this guard measured nothing"
    for sid, req in reqs.items():
        assert sweep.requirement_env_for(sid) == pre.requirement_env(req) != {}, (
            f"{sid} declares `requires: {req}` but the sweep provides nothing "
            f"for it — it will decline every run")


# ---------- ARMED: the sweep refuses a selection it cannot measure ----------

def _drive_main(monkeypatch, tmp_path, scen_specs, argv, home, probes=None,
                rep=None):
    """sweep.main() end-to-end over a tmp scenarios tree, with scenario
    execution recorded rather than performed. Returns (rc, ran)."""
    ran: list[str] = []
    monkeypatch.setattr(sweep, "SCEN", _scen_tree(tmp_path, scen_specs))
    monkeypatch.setattr(sweep, "BASELINES", tmp_path / "baselines")
    monkeypatch.setattr(sweep, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(sweep, "check_eval_home", lambda: [])
    monkeypatch.setattr(sweep, "_substrate_mod", lambda: _FakeSubstrate())
    monkeypatch.setattr(sweep, "prune_runs", lambda: 0)
    monkeypatch.setenv("ABA_HOME", str(home))
    p = probes or _ok_probes()
    monkeypatch.setattr(shutil, "which", p["which"])
    monkeypatch.setattr(pre, "scontrol_ping", p["ping"])

    def _run(sid, mode):
        ran.append(sid)
        return dict(rep) if rep else {"mechanical": {"pass": 2, "total": 2},
                                      "report": []}

    monkeypatch.setattr(sweep, "run_scenario", _run)
    monkeypatch.setattr(sys, "argv", ["sweep.py", "--no-prune"] + argv)
    return sweep.main(), ran


class _FakeSubstrate:
    """A stub that REFUSES to be more permissive than the real module: it
    answers the two calls the sweep makes and nothing else."""
    def stamp(self):
        return "weft=fake@/nowhere"

    def check_substrate(self):
        return []


_SLURM_SPECS = {"needs_slurm": "requires: slurm\nsteps: [a]\n",
                "plain": "steps: [a]\n"}


def test_sweep_refuses_when_the_requirement_cannot_be_honoured(
        monkeypatch, tmp_path, capsys):
    """ARMED — the whole point. On a host with no Slurm the three scheduler
    scenarios decline and the sweep reports a green headline over a selection it
    never measured. The assertion is on the ACTION: nothing ran, and the exit
    code is a setup error."""
    home = _home(tmp_path, slurm_site=False)      # no declared slurm site
    rc, ran = _drive_main(monkeypatch, tmp_path, _SLURM_SPECS, [],
                          home, probes={"which": lambda b: None,
                                        "ping": lambda: False})
    assert rc == 2, f"a sweep that cannot measure its selection exited {rc}"
    assert ran == [], f"scenarios ran after a refused pre-flight: {ran}"
    assert "SETUP-ERROR" in capsys.readouterr().out


def test_sweep_runs_when_the_requirement_is_satisfiable(monkeypatch, tmp_path):
    """The other side — a good host must not be refused, or the guard gets
    disabled the first time it cries wolf."""
    rc, ran = _drive_main(monkeypatch, tmp_path, _SLURM_SPECS, [],
                          _home(tmp_path))
    assert rc == 0
    assert ran == ["needs_slurm", "plain"], ran


def test_allow_declined_proceeds_knowingly(monkeypatch, tmp_path, capsys):
    """The escape hatch must exist (a refusal with no override gets patched out
    by whoever needs to run on a laptop) and must be LOUD."""
    home = _home(tmp_path, slurm_site=False)
    rc, ran = _drive_main(monkeypatch, tmp_path, _SLURM_SPECS,
                          ["--allow-declined"], home,
                          probes={"which": lambda b: None, "ping": lambda: False})
    assert rc == 0 and ran == ["needs_slurm", "plain"]
    assert "unmet requirement" in capsys.readouterr().out


def test_a_selection_with_no_requirements_is_never_refused(
        monkeypatch, tmp_path, capsys):
    """WIDE, degenerate: no scenario in the selection declares `requires:` at
    all. The check is vacuous — it must say so and let the sweep run, on a host
    that has no Slurm whatsoever."""
    rc, ran = _drive_main(monkeypatch, tmp_path, {"plain": "steps: [a]\n"}, [],
                          _home(tmp_path, slurm_site=False),
                          probes={"which": lambda b: None, "ping": lambda: False})
    assert rc == 0 and ran == ["plain"]
    assert "VACUOUS" in capsys.readouterr().out


def test_deselecting_the_requiring_scenario_lifts_the_refusal(
        monkeypatch, tmp_path):
    """WIDE: pre-flight keys on the SELECTION, not on the tree. A --only run
    that excludes the requiring scenario must not be blocked by it."""
    rc, ran = _drive_main(monkeypatch, tmp_path, _SLURM_SPECS, ["--only", "plain"],
                          _home(tmp_path, slurm_site=False),
                          probes={"which": lambda b: None, "ping": lambda: False})
    assert rc == 0 and ran == ["plain"]


# ---------- classification: declined is not a credential failure ----------
# (The row-level guards live beside their siblings in
#  tests/test_sweep_baseline_honesty.py — infra_classes, score_of, --accept.
#  What belongs HERE is the end-to-end console wiring, which needs the tmp
#  scenarios tree + host probes this file already builds.)

def test_declined_rows_do_not_trigger_the_credentials_banner(
        monkeypatch, tmp_path, capsys):
    """End-to-end through main(): with a declined row present and no credential
    failure, the console must not send the reader after fresh creds. (The
    wiring guard — infra_classes could be right while main() still printed the
    old bucket.)"""
    rc, _ = _drive_main(monkeypatch, tmp_path, _SLURM_SPECS, [], _home(tmp_path),
                        rep={"_error": "NOT-RUN: precondition unmet — [skip] "
                                       "needs_slurm requires the Slurm submitter",
                             "_skipped": True, "_infra": 1})
    out = capsys.readouterr().out
    assert "CREDENTIAL/RATE-LIMIT" not in out, (
        "a declined scenario was reported as a credential failure")
    assert "DECLINED" in out and "needs_slurm" in out.split("DECLINED")[1]
    assert rc in (0, 1)
    assert json.dumps(sweep.score_of(          # the row stays scorecard-writable
        {"_error": "NOT-RUN: precondition unmet", "_skipped": True, "_infra": 1}))
