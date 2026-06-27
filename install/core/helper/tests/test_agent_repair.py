"""Tier-0 agent repair: system probe, claude discovery, `claude -p` invocation
construction, the repair orchestration (injectable runner — no real CLI), and
the Executor failure → repair → retry loop.
"""
from __future__ import annotations
import os
from pathlib import Path

import pytest

from aba_installer import agent_repair as ar
from aba_installer.playbook import Executor, Playbook, Step


@pytest.fixture
def seeded_credential(tmp_path):
    """Seed a CLAUDE_CODE_OAUTH_TOKEN in $ABA_HOME/config.env so _run_agent's
    no-credential gate (commit 2) lets the runner be invoked. Tests that
    specifically exercise the no-credential or auth-failure path skip this."""
    (tmp_path / "config.env").write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-fixture\n"
    )
    yield


# ─── probe + discovery ──────────────────────────────────────────────────────
def test_probe_system_has_core_keys():
    p = ar.probe_system()
    for k in ("os", "macos_version", "arch", "has_git", "has_curl", "has_xcode_clt"):
        assert k in p, f"probe missing {k}"
    assert p["os"] == "macOS"
    assert isinstance(p["has_git"], bool)


def test_claude_path_prefers_aba_home_bin(tmp_path, monkeypatch):
    home = tmp_path / "aba"
    (home / "bin").mkdir(parents=True)
    binp = home / "bin" / "claude"
    binp.write_text("#!/bin/sh\n")
    os.chmod(binp, 0o755)
    monkeypatch.setenv("ABA_HOME", str(home))
    assert ar.claude_path() == str(binp)


def test_claude_path_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "nope"))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))   # no claude on PATH
    monkeypatch.setattr(ar.Path, "home", staticmethod(lambda: tmp_path / "nohome"))
    assert ar.claude_path() is None


# ─── ensure_claude (detect-or-install) ──────────────────────────────────────
def test_ensure_claude_returns_existing_without_installing(monkeypatch):
    monkeypatch.setattr(ar, "claude_path", lambda: "/x/aba/bin/claude")
    called = []
    got = ar.ensure_claude(installer=lambda: called.append(1))
    assert got == "/x/aba/bin/claude"
    assert called == [], "must not install when claude already present"


def test_ensure_claude_installs_then_redetects(monkeypatch):
    state = {"installed": False}
    monkeypatch.setattr(ar, "claude_path",
                        lambda: "/x/bin/claude" if state["installed"] else None)
    def installer():
        state["installed"] = True
    events = []
    got = ar.ensure_claude(installer=installer, on_event=lambda n, p: events.append((n, p)))
    assert got == "/x/bin/claude"
    assert any(p.get("phase") == "bootstrap" for _, p in events)


def test_ensure_claude_returns_none_if_install_fails(monkeypatch):
    monkeypatch.setattr(ar, "claude_path", lambda: None)
    def installer():
        raise RuntimeError("network down")
    assert ar.ensure_claude(installer=installer) is None


# ─── invocation construction ────────────────────────────────────────────────
def test_build_claude_argv_has_scoped_flags():
    argv = ar.build_claude_argv("claude", prompt="fix it", add_dir="/x/aba")
    assert argv[:3] == ["claude", "-p", "fix it"]
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--append-system-prompt-file" in argv
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Bash(xattr *)" in allowed and "Read" in allowed
    assert "--add-dir" in argv and "/x/aba" in argv
    assert "--bare" not in argv          # bare ignores CLAUDE_CODE_OAUTH_TOKEN
    assert "--output-format" in argv


def test_build_claude_argv_stream_mode():
    argv = ar.build_claude_argv("claude", prompt="p", stream=True)
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv


# ─── stream-json parsing (#2) ───────────────────────────────────────────────
def test_summarize_stream_event_surfaces_text_and_tools():
    tool_evt = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "xattr -d com.apple.quarantine /x/micromamba"}}]}}
    s = ar._summarize_stream_event(tool_evt)
    assert "🔧 Bash" in s and "xattr -d" in s
    text_evt = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Cleared the quarantine flag."}]}}
    assert "Cleared the quarantine" in ar._summarize_stream_event(text_evt)
    assert ar._summarize_stream_event({"type": "result", "result": "done"}) is None
    assert ar._summarize_stream_event({"type": "system", "subtype": "init"}) is None


def test_consume_stream_emits_actions_and_returns_final():
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"xattr -d com.apple.quarantine /x/mm"}}]}}',
        '{"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Removed quarantine."}]}}',
        '{"type":"result","subtype":"success","result":"Fixed.","is_error":false}',
    ]
    events = []
    final = ar._consume_stream(lines, lambda n, p: events.append(p["message"]))
    assert final["result"] == "Fixed." and final["is_error"] is False
    assert any("xattr -d" in m for m in events)
    assert any("Removed quarantine" in m for m in events)
    assert all("tool_result" not in m for m in events)   # verbose results not surfaced


# ─── pre-flight (#3) ─────────────────────────────────────────────────────────
def test_run_preflight_invokes_claude(monkeypatch, seeded_credential):
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    seen = {}
    def runner(argv, *, cwd, env):
        seen["argv"] = argv
        return {"returncode": 0, "result": "No blockers found; quarantine clear."}
    events = []
    out = ar.run_preflight("install-micromamba: Install micromamba; create-env: Create env",
                           cwd="/x/aba", runner=runner,
                           on_event=lambda n, p: events.append((n, p)))
    assert out.attempted and out.ok
    assert "PRE-FLIGHT" in seen["argv"][2]            # the preflight prompt
    assert any(p.get("phase") == "start" for _, p in events)


# ─── ABA credential pass-through (#1) ───────────────────────────────────────
def test_run_repair_passes_aba_credential_into_runner_env(tmp_path, monkeypatch):
    """config.env's CLAUDE_CODE_OAUTH_TOKEN must reach claude -p's env so the
    repair agent authenticates via ABA's existing credential (not ~/.claude)."""
    # ABA_HOME is tmp_path via the autouse fixture; write a minimal config.env
    # in the shape auth.py emits.
    (tmp_path / "config.env").write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test-12345\n"
    )
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    seen = {}
    def runner(argv, *, cwd, env):
        seen["env"] = env
        return {"returncode": 0, "result": "ok"}
    out = ar.run_repair("s", "t", "cmd", "err", runner=runner)
    assert out.attempted and out.ok
    assert seen["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat-test-12345"
    assert seen["env"].get("DISABLE_AUTOUPDATER") == "1"


def test_aba_credential_env_prefers_oauth_store_over_config(tmp_path, monkeypatch):
    """The refreshable OAuth store ($ABA_HOME/oauth.json) outranks config.env —
    matches backend/core/llm.py:_oauth_bearer priority."""
    (tmp_path / "config.env").write_text(
        "export CLAUDE_CODE_OAUTH_TOKEN=from-config\n"
    )
    import time as _t
    (tmp_path / "oauth.json").write_text(
        '{"access_token": "from-store", "refresh_token": "rt",'
        f' "expires_at": {_t.time() + 3600}}}'
    )
    creds = ar._aba_credential_env()
    assert creds == {"CLAUDE_CODE_OAUTH_TOKEN": "from-store"}


def test_aba_credential_env_falls_back_to_api_key(tmp_path):
    (tmp_path / "config.env").write_text(
        "export ANTHROPIC_API_KEY=sk-ant-api03-abc\n"
    )
    creds = ar._aba_credential_env()
    assert creds == {"ANTHROPIC_API_KEY": "sk-ant-api03-abc"}


def test_aba_credential_env_empty_when_unconfigured():
    # autouse fixture gives us a fresh ABA_HOME with no config.env / oauth.json
    assert ar._aba_credential_env() == {}


# ─── fail-fast on no usable session (#2) ────────────────────────────────────
def test_run_repair_skips_when_no_aba_credential(monkeypatch):
    """Issue #2: no config.env / oauth.json → don't even invoke claude. Return
    attempted=False so the executor halts cleanly instead of retrying a doomed
    repair (which used to loop on 'Not logged in')."""
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    called = []
    out = ar.run_repair("s", "t", "cmd", "err",
                        runner=lambda *a, **k: called.append(1) or {})
    assert out.attempted is False
    assert "no aba credential" in out.reason
    assert called == [], "runner must not run when no credential is configured"


def test_run_repair_skips_on_auth_failure_signature(monkeypatch, seeded_credential):
    """Claude ran but came back 'Not logged in' (stale/rejected token). Treat as
    no-attempt — the credential isn't going to fix itself between retries."""
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    def runner(argv, *, cwd, env):
        return {"returncode": 1, "result": "Not logged in · Please run /login"}
    events = []
    out = ar.run_repair("s", "t", "cmd", "err", runner=runner,
                        on_event=lambda n, p: events.append((n, p)))
    assert out.attempted is False
    assert "claude auth failed" in out.reason
    assert any(p.get("phase") == "skip" for _, p in events)


def test_run_repair_skips_on_invalid_api_key_signature(monkeypatch, seeded_credential):
    """The other side of the auth-failure coin: API mode with a bad key."""
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    out = ar.run_repair("s", "t", "cmd", "err",
                        runner=lambda *a, **k: {"returncode": 1,
                                                "result": "Invalid API key · Unauthorized"})
    assert out.attempted is False


def test_repair_hook_short_circuits_when_no_credential(tmp_path, monkeypatch):
    """make_repair_hook must not even bootstrap claude when there's no credential
    — no point downloading a binary we can't authenticate."""
    installs = []
    monkeypatch.setattr(ar, "_default_installer",
                        lambda: installs.append(1))
    monkeypatch.setattr(ar, "claude_path", lambda: None)
    events = []
    hook = ar.make_repair_hook(cwd=str(tmp_path), runner=lambda *a, **k: {"returncode": 0},
                               on_event=lambda n, p: events.append((n, p)),
                               ensure=True)
    pb = Playbook(steps=[_step("false")])
    results = Executor(pb, on_step_failed=hook, max_repair_attempts=2).run_all()
    assert not results[-1].ok, "step still fails — no repair happened"
    assert installs == [], "must not bootstrap claude when no credential"
    assert any(p.get("phase") == "skip" for _, p in events)


# ─── run_repair ─────────────────────────────────────────────────────────────
def test_run_repair_skips_when_no_claude(monkeypatch):
    monkeypatch.setattr(ar, "claude_path", lambda: None)
    called = []
    out = ar.run_repair("create-env", "Create env", "micromamba create …", "boom",
                        runner=lambda *a, **k: called.append(1) or {})
    assert out.attempted is False and out.ok is False
    assert "not available" in out.reason
    assert called == [], "runner must not run without claude"


def test_run_repair_invokes_claude_and_reports_ok(monkeypatch, seeded_credential):
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    seen = {}
    def runner(argv, *, cwd, env):
        seen["argv"] = argv; seen["cwd"] = cwd
        return {"returncode": 0, "result": "Cleared com.apple.quarantine on micromamba."}
    events = []
    out = ar.run_repair("install-micromamba", "Install micromamba",
                        'curl … micromamba', "killed: 9", cwd="/x/aba",
                        runner=runner, on_event=lambda n, p: events.append((n, p)))
    assert out.attempted and out.ok
    assert "quarantine" in out.diagnosis
    assert seen["cwd"] == "/x/aba"
    assert "install-micromamba" in seen["argv"][2]        # prompt mentions the step
    assert any(p.get("phase") == "done" for _, p in events)


def test_run_repair_marks_failure_on_error(monkeypatch, seeded_credential):
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    out = ar.run_repair("s", "t", "cmd", "err",
                        runner=lambda *a, **k: {"returncode": 1, "is_error": True, "result": "nope"})
    assert out.attempted and not out.ok


# ─── Executor failure → repair → retry ──────────────────────────────────────
def _step(cmd: str, id: str = "probe-step") -> Step:
    return Step(id=id, title=id, why="t", commands=[cmd], timeout_seconds=30)


def test_executor_retries_after_repair_fixes_the_system(tmp_path, monkeypatch, seeded_credential):
    """The step fails until the repair 'fixes the system' (creates a marker the
    command checks for), then the retry passes — proving the whole chain."""
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    marker = tmp_path / "fixed"
    step = _step(f'test -f "{marker}"')          # fails until marker exists

    def runner(argv, *, cwd, env):               # 'Claude' fixes the system
        marker.touch()
        return {"returncode": 0, "result": "created the missing marker"}

    events = []
    hook = ar.make_repair_hook(cwd=str(tmp_path), runner=runner,
                               on_event=lambda n, p: events.append((n, p)))
    pb = Playbook(steps=[step])
    results = Executor(pb, on_step_failed=hook, max_repair_attempts=1).run_all()
    assert results[-1].ok, "step should pass after repair created the marker"
    assert marker.exists()
    assert any(n == "repair" for n, _ in events)


def test_executor_gives_up_after_max_attempts(monkeypatch, seeded_credential):
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    calls = {"n": 0}
    def runner(argv, *, cwd, env):
        calls["n"] += 1
        return {"returncode": 0, "result": "tried but the command still fails"}
    hook = ar.make_repair_hook(runner=runner)
    pb = Playbook(steps=[_step("false")])        # never succeeds
    results = Executor(pb, on_step_failed=hook, max_repair_attempts=2).run_all()
    assert not results[-1].ok
    assert calls["n"] == 2, f"repair should run exactly max_repair_attempts times, got {calls['n']}"


def test_repair_hook_ensure_bootstraps_claude_on_first_failure(tmp_path, monkeypatch, seeded_credential):
    """The control.py path: ensure=True installs `claude` on the first failure
    (not before), then repairs + retries."""
    state = {"installed": False}
    monkeypatch.setattr(ar, "claude_path", lambda: "claude" if state["installed"] else None)
    installs = []
    def fake_install():
        installs.append(1); state["installed"] = True
    monkeypatch.setattr(ar, "_default_installer", fake_install)
    marker = tmp_path / "m"
    def runner(argv, *, cwd, env):
        marker.touch(); return {"returncode": 0, "result": "fixed"}
    hook = ar.make_repair_hook(cwd=str(tmp_path), runner=runner, ensure=True)
    pb = Playbook(steps=[_step(f'test -f "{marker}"')])
    results = Executor(pb, on_step_failed=hook, max_repair_attempts=1).run_all()
    assert results[-1].ok, "step should pass after bootstrap+repair"
    assert installs == [1], "claude bootstrapped exactly once, on the failure"


def test_executor_unchanged_when_no_hook():
    pb = Playbook(steps=[_step("false")])
    results = Executor(pb).run_all()             # no repair hook → legacy behaviour
    assert not results[-1].ok


# ─── control wiring: pre-flight is flag-gated ───────────────────────────────
def test_control_preflight_runs_unless_explicit_opt_out(monkeypatch):
    """Default ON as of 2026-06-11. Env unset → enabled; '0'/'false'/etc → off.
    Pre-flight gracefully no-ops when claude / credentials are unavailable,
    so default-on is safe for users without a Claude session."""
    from aba_installer import control
    monkeypatch.setattr(ar, "ensure_claude", lambda **k: "claude")
    calls = []
    monkeypatch.setattr(ar, "run_preflight", lambda *a, **k: calls.append(1))
    pb = Playbook(steps=[_step("true")])

    # Default: env unset → enabled
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)
    control._run_preflight_if_enabled(pb, lambda n, p: None)
    assert calls == [1], "pre-flight must run by default (env unset)"

    # Explicit opt-out: '0' disables
    calls.clear()
    monkeypatch.setenv("ABA_INSTALL_AGENT_REPAIR", "0")
    control._run_preflight_if_enabled(pb, lambda n, p: None)
    assert calls == [], "ABA_INSTALL_AGENT_REPAIR=0 must disable pre-flight"

    # Explicit opt-in keeps working
    calls.clear()
    monkeypatch.setenv("ABA_INSTALL_AGENT_REPAIR", "1")
    control._run_preflight_if_enabled(pb, lambda n, p: None)
    assert calls == [1], "explicit '1' must still enable"


# ─── control wiring: preflight skipped for update by default ───────────────
def test_preflight_skipped_for_update_kind_by_default(monkeypatch):
    """User feedback 2026-06-13: adaptive preflight took ~half the update
    time with no payoff (system already passed install). Default for
    kind='update' is OFF; opt-in via ABA_INSTALL_AGENT_REPAIR_UPDATE_PREFLIGHT=1.
    Repair hook still active on step failure either way."""
    from aba_installer import control
    monkeypatch.setattr(ar, "ensure_claude", lambda **k: "claude")
    calls = []
    monkeypatch.setattr(ar, "run_preflight", lambda *a, **k: calls.append(1))
    pb = Playbook(steps=[_step("true")])
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR_UPDATE_PREFLIGHT", raising=False)

    # update kind → skipped even though agent_repair is enabled overall
    control._run_preflight_if_enabled(pb, lambda n, p: None, kind="update")
    assert calls == [], "update kind must skip preflight by default"

    # install kind → still runs (regression guard)
    control._run_preflight_if_enabled(pb, lambda n, p: None, kind="install")
    assert calls == [1], "install kind must still run preflight"

    # update + explicit opt-in → runs
    calls.clear()
    monkeypatch.setenv("ABA_INSTALL_AGENT_REPAIR_UPDATE_PREFLIGHT", "1")
    control._run_preflight_if_enabled(pb, lambda n, p: None, kind="update")
    assert calls == [1], "explicit ABA_INSTALL_AGENT_REPAIR_UPDATE_PREFLIGHT=1 must enable"


def test_preflight_emits_step_start_and_step_end_for_checklist(monkeypatch):
    """The checklist UI seeds ○ items from step_planned and ✓-checks them
    on step_end. Pre-2026-06-13 the preflight emitted only 'repair' frames,
    so the user saw a long unexplained delay with no ✓ at the end. Now
    preflight wraps itself in step_start/step_end with id 'adaptive-preflight'."""
    from aba_installer import control
    monkeypatch.setattr(ar, "ensure_claude", lambda **k: "claude")
    # Stub run_preflight: claim success, no side effects.
    class _Outcome:
        ok = True
    monkeypatch.setattr(ar, "run_preflight", lambda *a, **k: _Outcome())
    pb = Playbook(steps=[_step("true")])
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)

    events: list[tuple[str, dict]] = []
    control._run_preflight_if_enabled(
        pb, lambda name, payload: events.append((name, payload)),
        kind="install",
    )

    pid = control.PREFLIGHT_STEP_ID
    starts = [p for (n, p) in events if n == "step_start" and p.get("step_id") == pid]
    ends   = [p for (n, p) in events if n == "step_end"   and p.get("step_id") == pid]
    assert len(starts) == 1, f"expected exactly one step_start for {pid}, got {events}"
    assert len(ends)   == 1, f"expected exactly one step_end   for {pid}, got {events}"
    assert ends[0].get("ok") is True


def test_preflight_step_end_carries_ok_false_on_exception(monkeypatch):
    """If run_preflight raises, we still emit step_end (ok=false) so the
    checklist doesn't get stuck on an active ○ forever."""
    from aba_installer import control
    monkeypatch.setattr(ar, "ensure_claude", lambda **k: "claude")
    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(ar, "run_preflight", _boom)
    pb = Playbook(steps=[_step("true")])
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)

    events: list[tuple[str, dict]] = []
    control._run_preflight_if_enabled(
        pb, lambda name, payload: events.append((name, payload)),
        kind="install",
    )
    pid = control.PREFLIGHT_STEP_ID
    ends = [p for (n, p) in events if n == "step_end" and p.get("step_id") == pid]
    assert ends and ends[0].get("ok") is False, events


# ─── planned-steps helper prepends the virtual preflight item ──────────────
def test_planned_steps_prepends_adaptive_preflight_for_install(monkeypatch):
    """The browser-side checklist is seeded from step_planned. For
    install runs, the preflight needs its own ○ from the start."""
    from aba_installer import control
    pb = Playbook(steps=[_step("true", "step-a"), _step("true", "step-b")])
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)

    steps = control._planned_steps(pb, kind="install")
    assert steps[0]["id"] == control.PREFLIGHT_STEP_ID
    assert [s["id"] for s in steps[1:]] == ["step-a", "step-b"]


def test_planned_steps_no_preflight_for_update_by_default(monkeypatch):
    from aba_installer import control
    pb = Playbook(steps=[_step("true", "step-a"), _step("true", "step-b")])
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)
    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR_UPDATE_PREFLIGHT", raising=False)

    steps = control._planned_steps(pb, kind="update")
    assert [s["id"] for s in steps] == ["step-a", "step-b"]
    assert all(s["id"] != control.PREFLIGHT_STEP_ID for s in steps)


def test_planned_steps_no_preflight_when_repair_disabled(monkeypatch):
    """If the user opted out of agent repair globally, the virtual
    preflight item shouldn't appear in the checklist either."""
    from aba_installer import control
    pb = Playbook(steps=[_step("true", "step-a")])
    monkeypatch.setenv("ABA_INSTALL_AGENT_REPAIR", "0")
    steps = control._planned_steps(pb, kind="install")
    assert all(s["id"] != control.PREFLIGHT_STEP_ID for s in steps)
