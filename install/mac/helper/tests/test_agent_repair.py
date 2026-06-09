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
def test_run_preflight_invokes_claude(monkeypatch):
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


# ─── run_repair ─────────────────────────────────────────────────────────────
def test_run_repair_skips_when_no_claude(monkeypatch):
    monkeypatch.setattr(ar, "claude_path", lambda: None)
    called = []
    out = ar.run_repair("create-env", "Create env", "micromamba create …", "boom",
                        runner=lambda *a, **k: called.append(1) or {})
    assert out.attempted is False and out.ok is False
    assert "not available" in out.reason
    assert called == [], "runner must not run without claude"


def test_run_repair_invokes_claude_and_reports_ok(monkeypatch):
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


def test_run_repair_marks_failure_on_error(monkeypatch):
    monkeypatch.setattr(ar, "claude_path", lambda: "claude")
    out = ar.run_repair("s", "t", "cmd", "err",
                        runner=lambda *a, **k: {"returncode": 1, "is_error": True, "result": "nope"})
    assert out.attempted and not out.ok


# ─── Executor failure → repair → retry ──────────────────────────────────────
def _step(cmd: str) -> Step:
    return Step(id="probe-step", title="Probe", why="t", commands=[cmd], timeout_seconds=30)


def test_executor_retries_after_repair_fixes_the_system(tmp_path, monkeypatch):
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


def test_executor_gives_up_after_max_attempts(monkeypatch):
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


def test_repair_hook_ensure_bootstraps_claude_on_first_failure(tmp_path, monkeypatch):
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
def test_control_preflight_runs_only_when_flag_enabled(monkeypatch):
    from aba_installer import control
    monkeypatch.setattr(ar, "ensure_claude", lambda **k: "claude")
    calls = []
    monkeypatch.setattr(ar, "run_preflight", lambda *a, **k: calls.append(1))
    pb = Playbook(steps=[_step("true")])

    monkeypatch.delenv("ABA_INSTALL_AGENT_REPAIR", raising=False)
    control._run_preflight_if_enabled(pb, lambda n, p: None)
    assert calls == [], "pre-flight must not run when the flag is off"

    monkeypatch.setenv("ABA_INSTALL_AGENT_REPAIR", "1")
    control._run_preflight_if_enabled(pb, lambda n, p: None)
    assert calls == [1], "pre-flight must run when the flag is on"
