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


def _cap(kinds=None, tools=None, errors=None):
    return {"kinds": kinds or {}, "tools": tools or [],
            "errors": list(errors or [])}


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


def test_a_failed_turn_is_a_finding_about_the_deployment_not_the_probe():
    """The misdiagnosis that cost a whole verify run.

    Forty-two packages reported "the probe is reading the wrong event names"
    when every turn had errored in 0.3s and the parser was working perfectly —
    there were no tool events because nothing ran. The instrument accused
    ITSELF and the real cause was never printed. An error event present means
    the turn failed; that is a finding about the deployment."""
    import live_install_probe as lip
    cap = _cap(kinds={"done": 1, "error": 1, "manifest": 1, "usage": 1},
               errors=["{'type': 'error', 'error': 'rate_limit_error'}"])
    assert lip._instrument_fault(cap, True) is None, (
        "an errored turn is not the instrument going blind")
    failed = lip._turn_failed(cap)
    assert failed and "rate_limit_error" in failed, failed


def test_a_failed_turn_names_its_cause_not_just_a_count():
    """`turn_errors: 1` is not actionable. Forty-two identical failures with no
    reason carry the same information as one."""
    import live_install_probe as lip
    cap = _cap(kinds={"error": 2}, errors=["first reason", "second reason"])
    msg = lip._turn_failed(cap)
    assert "first reason" in msg and "+1 more" in msg, msg


def test_a_clean_turn_is_not_reported_as_failed():
    import live_install_probe as lip
    assert lip._turn_failed(_cap(kinds={"text": 1}, tools=["run_r"])) is None


def test_turn_failed_gates_the_release():
    """WIDE: a new verdict that is not in the failure set is a verdict that
    lets a broken release through — the whole point of the gate."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    tail = src[src.index("    bad = [r for r in rows"):]
    assert '"turn_failed"' in tail[:400], (
        "turn_failed must be in the set that fails the gate")


def test_a_run_that_installed_nothing_is_not_a_pass():
    """The gate that could not fail.

    `deploy.sh verify --install` passed `--pack-provided-only`: it asked only
    for libraries the pack already ships, so every row came back
    `ready_from_pack` and 46/46 read as a green install gate while the install
    path had never executed once. Two defects walked straight through it — an
    isolated env with no C++ compiler, and a cran toolchain with no libxml2
    headers — and both were found by a user on the first real request.

    A measured zero and an unmeasured zero look identical in a results table.
    Scope that CAN install and installed nothing is unmeasured."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    tail = src[src.index("    installed_rows = ["):]
    assert "unarmed" in tail and "pack_provided_only" in tail
    ret = src[src.index("    return 1 if ("):]
    assert "unarmed" in ret[:80], (
        "the arming check must GATE the exit code, not just print")


def test_recognition_only_scope_is_still_allowed_to_install_nothing():
    """WIDE: the other side. `--pack-provided-only` is a legitimate question —
    'does the pack still recognise what it ships' — and zero installs is the
    CORRECT answer there. Arming it would make the honest scope unusable."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    line = [l for l in src.splitlines() if l.strip().startswith("unarmed = ")]
    assert line and "not a.pack_provided_only" in line[0], line


def test_the_shipped_gate_asks_for_something_the_pack_lacks():
    """The arming check only bites if the DEFAULT scope can install. A default
    of pack-provided-only would satisfy every assertion above and still test
    nothing — which is exactly how this shipped."""
    vsh = None
    for cand in (REPO.parent / "aba-vbc" / "verify.sh",):
        if cand.exists():
            vsh = cand.read_text()
    if vsh is None:
        pytest.skip("aba-vbc checkout not alongside this one")
    assert "--install) INSTALL=mixed" in vsh, (
        "--install must default to a scope that installs")
    mixed = [l for l in vsh.splitlines() if l.startswith("_MIXED_SET=")]
    assert mixed, "no default corpus"
    names = mixed[0].split('"')[1].split(",")
    assert "scrublet" in names, (
        "the corpus must include the sdist-build path — the one that failed "
        "live with a missing g++")
    assert len(names) >= 4


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


def test_a_turn_that_ends_by_submitting_a_job_is_not_a_failure(monkeypatch):
    """An async turn has not finished when the stream closes.

    The sweep's own first results scored `unavailable` on an entry that had
    spent sixteen minutes, built an environment and submitted a background job:
    real work, judged at the instant the SSE stream closed, before the job it
    had just started could produce an exec record. The verdict said the
    deployment could not provide the tool; what it actually measured was the
    probe's impatience.

    A submitted job is the proof for that shape of turn — so await it, and only
    then decide."""
    import live_install_probe as lip

    class _C:
        def post(self, path, **kw):
            return type("R", (), {"json": lambda _s: {"id": "p1"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()

        def stream(self, *a, **kw):
            raise AssertionError("unused")

    # a turn that ran tools and submitted one job that COMPLETED
    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["ensure_capability", "run_python"],
        "errors": [], "text": [], "jobs": ["j1"], "cap_results": [],
        "kinds": {"tool_start": 2, "job_submitted": 1, "done": 1}})
    monkeypatch.setattr(lip, "_await_job",
                        lambda c, j, t: {"job_id": j, "status": "done",
                                         "site": "cluster"})
    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))

    row = lip.probe_one(_C(), {"name": "thing", "language": "python"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["verdict"] != "unavailable", row
    assert row["submitted_jobs"], row
    assert "job" in row["exec"], row["exec"]


def test_a_submitted_job_that_failed_is_still_a_failure(monkeypatch):
    """ARMED: awaiting the job must not become a way to pass on nothing."""
    import live_install_probe as lip

    class _C:
        def post(self, path, **kw):
            return type("R", (), {"json": lambda _s: {"id": "p1"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()

    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["run_python"], "errors": [], "text": [],
        "jobs": ["j1"], "cap_results": [], "kinds": {"tool_start": 1}})
    monkeypatch.setattr(lip, "_await_job",
                        lambda c, j, t: {"job_id": j, "status": "failed"})
    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))

    row = lip.probe_one(_C(), {"name": "thing", "language": "python"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["verdict"] == "unavailable", row


def test_the_row_names_which_tools_ran(monkeypatch):
    """A bare count cannot tell a probe gap from a product gap — both of the
    sweep's first failures had healthy tool activity."""
    import live_install_probe as lip

    class _C:
        def post(self, path, **kw):
            return type("R", (), {"json": lambda _s: {"id": "p1"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()

    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["ensure_capability", "ensure_capability",
                                  "search_bioconda"],
        "errors": [], "text": [], "jobs": [], "cap_results": [],
        "kinds": {"tool_start": 3}})
    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))
    row = lip.probe_one(_C(), {"name": "thing", "language": "python"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["tool_names"] == ["ensure_capability", "search_bioconda"], row


def test_a_real_load_probe_counts_as_proof(monkeypatch):
    """`inspect_env(name=…)` MEASURES an environment; it does not claim about one.

    It runs a real requireNamespace/import in the runtime the job would use and
    returns {loads, version} — the same question this probe asks, answered by
    the platform's own probe. Excluding it scored `reticulate` as `unavailable`
    on a deployment where it loads at 1.46.0: the agent looked, found it
    present, and correctly did nothing, and the probe recorded that as a failure
    to provide it."""
    import live_install_probe as lip

    class _C:
        def post(self, path, **kw):
            return type("R", (), {"json": lambda _s: {"id": "p1"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()

    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))
    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["inspect_env"], "errors": [], "text": [],
        "jobs": [], "kinds": {"tool_start": 1},
        "cap_results": [{"tool": "inspect_env", "status": "ok",
                         "loads": True, "version": "1.46.0"}]})
    row = lip.probe_one(_C(), {"name": "thing", "language": "r"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["verdict"] == "ready_from_pack", row
    assert row["proof"] == "verified", row


def test_a_probe_that_ran_and_found_nothing_is_not_proof(monkeypatch):
    """ARMED, and the trap: inspect_env returns `status: "ok"` meaning THE PROBE
    RAN, with `loads: false` meaning the package is absent. Keying on status
    would turn an honest "I checked, it is not there" into evidence that it is."""
    import live_install_probe as lip

    class _C:
        def post(self, path, **kw):
            return type("R", (), {"json": lambda _s: {"id": "p1"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()

    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))
    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["inspect_env"], "errors": [], "text": [],
        "jobs": [], "kinds": {"tool_start": 1},
        "cap_results": [{"tool": "inspect_env", "status": "ok",
                         "loads": False, "version": None}]})
    row = lip.probe_one(_C(), {"name": "thing", "language": "r"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["verdict"] == "unavailable", row


def _client():
    class _C:
        def post(self, path, **kw):
            return type("R", (), {"status_code": 200,
                                  "json": lambda _s: {"id": "p1"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()
    return _C()


def test_a_clean_exec_alone_does_not_prove_the_package_is_there(monkeypatch):
    """An exec record says SOMETHING ran, not that THIS package is available.

    An agent explaining — in perfectly working R — that a library is not
    available produces a clean exec record. The sweep scored `BPCells` and
    `SeuratWrappers` as `ready_from_pack` on exactly that evidence; a direct
    requireNamespace against the published image reports both `absent`. Every
    "ready" total was inflated by however many of those there were, and the
    inflation ran in the direction that flattered the result."""
    import live_install_probe as lip
    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))
    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["run_r"], "errors": [], "text": [],
        "jobs": [], "cap_results": [], "kinds": {"tool_start": 1}})
    monkeypatch.setattr(lip, "_exec_ok", lambda c, p, r: (True, "1 clean exec"))
    row = lip.probe_one(_client(), {"name": "thing", "language": "r"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["verdict"] == "unverified", row
    assert row["proof"] == "unverified", row


def test_a_package_specific_verdict_is_proof(monkeypatch):
    """WIDE: the platform naming THIS package available still counts."""
    import live_install_probe as lip
    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))
    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["ensure_capability"], "errors": [], "text": [],
        "jobs": [], "cap_results": [{"status": "provided_by_pack"}],
        "kinds": {"tool_start": 1}})
    monkeypatch.setattr(lip, "_exec_ok", lambda c, p, r: (True, "1 clean exec"))
    row = lip.probe_one(_client(), {"name": "thing", "language": "r"},
                        timeout=1, projects_dir=None, pack_names={})
    assert row["verdict"] == "ready_from_pack", row
    assert row["proof"] == "verified", row


def test_unverified_counts_as_a_failure_not_a_pass():
    """ARMED: a new verdict that is not in the failure set is a new way to be
    silently green."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    bad = src[src.index("bad = [r for r in rows"):]
    assert '"unverified"' in bad[:400], (
        "the `unverified` verdict must be in the failure set, or it becomes a "
        "quiet pass for 'we could not tell'")


def test_a_core_library_used_directly_is_not_a_release_blocker():
    """numpy and pandas failed the gate for not being ASKED about.

    The agent skipped `ensure_capability` and just imported them — correct
    behaviour for a core library, and it worked in 10 and 7 seconds at zero
    cost. The probe scored both `unverified` and failed the release. A gate
    that cries wolf on the product working correctly is a gate people learn to
    override.
    """
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    blk = src[src.index('if not verdicts:'):]
    blk = blk[:blk.index('row["verdict"] = "unverified"')]
    assert 'row.get("pack_provided")' in blk, (
        "the exception must be gated on the SHIPPED PACKS, not on anything "
        "the turn said")
    assert "made == 0" in blk and "adds == 0" in blk, (
        "and on zero cost — an install that happened is not an assumption")


def test_the_absent_package_loophole_stays_shut():
    """WIDE, and the whole reason this is delicate. Two packages the pack does
    NOT contain once scored `ready_from_pack` off a clean exec. The new class
    must be unreachable for them: `pack_provided` is computed from the packs on
    disk, so an absent package cannot enter it however the turn behaves."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    assert '"assumed_from_pack"' in src
    blk = src[src.index('if row.get("pack_provided") and made == 0'):]
    blk = blk[:blk.index("return row")]
    assert "assumed_from_pack" in blk and "verified" not in blk.replace(
        '"assumed"', ''), "an assumption must never be reported as verified"


def test_assumed_is_reported_as_its_own_class():
    """It is not a pass and not a failure — it is a third thing, and it has to
    be visible in the summary or it is just a silent downgrade."""
    src = (REPO / "regtest" / "harness" / "live_install_probe.py").read_text()
    order = src[src.index('for v in ("ready_from_pack"'):]
    order = order[:order.index(")")]
    assert "assumed_from_pack" in order, "absent from the printed summary"
    bad = src[src.index("    bad = [r for r in rows"):]
    bad = bad[:bad.index("]")]
    assert "assumed_from_pack" not in bad, "must not fail the gate"
