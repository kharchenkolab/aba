"""The install gate must not be disabled by a missing data file.

`live_install_probe --pack-provided-only` is the regression gate for a live
incident: a library the shipped base packs PROVE they load must cost zero
environments to "install". Its expectation comes from the packs themselves, so
it has to keep working when the (larger, optional) package matrix is absent.

It did not. `_load_matrix` raised SystemExit on a missing file, SystemExit is a
BaseException, and the `except Exception` around the lookup let it straight
through — so deleting a JSON file silently turned the gate off. That is the
same shape as the defect the gate exists to catch: an instrument that cannot
run reads as an instrument that found nothing wrong.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "regtest" / "harness"))


def test_pack_scope_survives_a_missing_matrix(monkeypatch, tmp_path, capsys):
    """THE regression: no matrix file, gate still enumerates the packs."""
    import live_install_probe as lip
    monkeypatch.setattr(sys, "argv", [
        "p", "--base", "http://127.0.0.1:1", "--pack-provided-only",
        "--matrix", str(tmp_path / "nope.json"), "--limit", "0"])
    # stop before any HTTP: we are testing enumeration, not the turns
    monkeypatch.setattr(lip, "probe_one",
                        lambda *a, **k: {"name": k.get("entry", {}).get("name", "x"),
                                         "verdict": "ready_from_pack"})
    try:
        lip.main()
    except SystemExit as e:            # must NOT be the "no matrix" bail-out
        assert "no matrix file" not in str(e), e
    out = capsys.readouterr().out
    assert "pack-provided names known:" in out, out
    n = int(out.split("pack-provided names known:")[1].split()[0])
    assert n > 10, f"expected the shipped packs to advertise many names, got {n}"


def test_pack_expectation_comes_from_the_shipped_packs():
    """The gate must state an INDEPENDENT expectation.

    Reading it from the running server would mean asking the system under test
    what it believes it provides — which is precisely the belief that was
    wrong."""
    import live_install_probe as lip
    provided = lip.pack_provided()
    assert len(provided) > 10, provided
    assert set(provided.values()) <= {"r", "python"}, provided
    # every name must be a real load target, not a conda package name
    assert not [n for n in provided if n.startswith(("r-", "bioconductor-"))], (
        "pack_provided() must yield LIBRARY names (what a user asks for), not "
        "conda package names: " + str([n for n in provided
                                       if n.startswith(("r-", "bioconductor-"))]))


def test_a_matrix_file_enriches_but_does_not_gate(tmp_path):
    """WIDE: merging is additive and later files win on conflicts."""
    import live_install_probe as lip
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"entries": [
        {"name": "alpha", "language": "python", "ecosystem": "pypi", "package": "alpha"},
        {"name": "beta", "language": "r", "ecosystem": "cran", "package": "r-beta"}]}))
    b.write_text(json.dumps({"entries": [
        {"name": "alpha", "language": "python", "ecosystem": "pypi",
         "package": "alpha-real", "n_recipes": 4}]}))
    got = {e["name"]: e for e in lip._load_matrix(f"{a},{b}")}
    assert set(got) == {"alpha", "beta"}
    assert got["alpha"]["package"] == "alpha-real"     # later source wins
    assert got["alpha"]["n_recipes"] == 4
    assert got["beta"]["package"] == "r-beta"          # earlier survives


def test_missing_matrix_is_a_normal_exception():
    """So a caller can CATCH it. SystemExit could not be caught by the
    `except Exception` that was meant to keep the gate alive."""
    import live_install_probe as lip
    with pytest.raises(FileNotFoundError):
        lip._load_matrix("/nonexistent/one.json,/nonexistent/two.json")


def _cap(kinds=None, tools=None):
    return {"kinds": kinds or {}, "tools": tools or []}


def test_probe_reports_its_own_blindness_not_a_finding():
    """A measured zero and an unmeasured zero must not look alike.

    The probe read the wrong SSE event names — `tool_call` where the server
    emits `tool_start` — so it recorded zero tool calls for all 33 packages in
    its first real run. That reads in the results table as "the agent never
    checked anything", which is a striking finding and was entirely an artifact
    of the parser. It also meant `run_id` was never set, so the approval-gate
    resume loop never ran and any turn that paused for approval was abandoned
    half-finished and scored as a failure of the deployment.

    An instrument that cannot detect its own blindness will keep producing
    confident findings about nothing."""
    import live_install_probe as lip
    # ran something, but the parser saw no tool events => the parser is wrong
    fault = lip._instrument_fault(_cap(kinds={"text": 4, "tool_start": 2}), True)
    assert fault and "wrong event names" in fault, fault
    # nothing parsed at all
    assert lip._instrument_fault(_cap(), False), "empty stream must be a fault"


def test_a_genuinely_quiet_turn_is_not_a_fault():
    """WIDE: an advice turn that runs nothing is a legitimate observation, not
    an instrument failure — the tell is exec-records-without-tool-events."""
    import live_install_probe as lip
    assert lip._instrument_fault(_cap(kinds={"text": 3}), False) is None


def test_a_normal_turn_is_not_a_fault():
    import live_install_probe as lip
    assert lip._instrument_fault(
        _cap(kinds={"text": 3, "tool_start": 1}, tools=["run_r"]), True) is None


def test_the_parser_accepts_the_event_names_the_server_actually_emits():
    """Pin the names against the probe that is known to work against a real
    server (live_surface_probe), so the two cannot drift apart again."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    assert '"tool_start"' in src, "the server emits tool_start; the probe must read it"
    # run_id must be taken from any event, not gated on one type
    assert 'if ev.get("run_id"):' in src, (
        "run_id rides on any event — gating it on a single event type left the "
        "approval-gate resume loop dead")


def test_a_session_install_is_not_a_free_answer(tmp_path):
    """Cost has two shapes and only one of them mints a named env.

    `ensure_capability` can satisfy a request by installing into the project's
    DEFAULT weft session, which creates no named env at all. Counting named
    envs alone therefore reported "envs=0, ready_from_pack" — cost nothing —
    for a request that had just resolved, fetched and solved a package. It
    flatters the result in the one direction that matters, and had the original
    incident taken the session lane instead of the isolated-env lane, this
    probe would have called it free."""
    import live_install_probe as lip
    pid = "p1"
    d = tmp_path / pid
    d.mkdir()
    reg = d / "weft_envs.json"

    reg.write_text(json.dumps({"envs": {}, "default": {}}))
    assert lip._env_count(tmp_path, pid) == (0, 0)

    # a session install: no named env, one recorded addition
    reg.write_text(json.dumps(
        {"envs": {}, "default": {"python": {"additions": [{"specs": ["x"]}]}}}))
    assert lip._env_count(tmp_path, pid) == (0, 1)

    # an isolated env: named env, no session addition
    reg.write_text(json.dumps({"envs": {"iso": {}}, "default": {}}))
    assert lip._env_count(tmp_path, pid) == (1, 0)


def test_unreadable_registry_is_none_not_zero(tmp_path):
    """ARMED: an unmeasured cost must not read as no cost."""
    import live_install_probe as lip
    d = tmp_path / "p2"
    d.mkdir()
    (d / "weft_envs.json").write_text("{ this is not json")
    assert lip._env_count(tmp_path, "p2") is None
    assert lip._env_count(None, "p2") is None
