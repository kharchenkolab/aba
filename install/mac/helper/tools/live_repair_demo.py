#!/usr/bin/env python3
"""LIVE end-to-end demo of Tier-0 agent repair against the REAL `claude` CLI.

It injects a realistic, deterministic failure (a **quarantined** stand-in
binary: the step checks `xattr` and fails while the com.apple.quarantine flag is
present), then lets Claude Code diagnose it and run `xattr -d` to fix it, and
re-runs the step. You should watch the agent's actions stream live.

⚠ This makes REAL `claude -p` calls and spends tokens on the configured
subscription. It runs claude with CLAUDE_CONFIG_DIR pointed at a throwaway dir so
it NEVER touches your real ~/.claude (verified before/after). It needs:
  • `claude` on PATH (or ~/.aba/bin/claude),
  • a CLAUDE_CODE_OAUTH_TOKEN (read from ~/.aba/config.env if not in the env).

Run:
    python install/mac/helper/tools/live_repair_demo.py
"""
from __future__ import annotations
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1] / "src"))   # helper/src


def _read_token() -> str | None:
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    cfg = Path.home() / ".aba" / "config.env"
    if cfg.exists():
        m = re.search(r'^export CLAUDE_CODE_OAUTH_TOKEN=(.*)$', cfg.read_text(), re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def _snapshot_dotclaude() -> str:
    home_claude = Path.home() / ".claude"
    if not home_claude.exists():
        return ""
    out = []
    for p in sorted(home_claude.rglob("*")):
        try:
            out.append(f"{p.stat().st_mtime_ns} {p}")
        except Exception:
            pass
    return "\n".join(out)


def main() -> int:
    from aba_installer.agent_repair import claude_path, make_repair_hook
    from aba_installer.playbook import Executor, Playbook, Step

    claude = claude_path()
    token = _read_token()
    if not claude:
        print("✗ no `claude` binary found (PATH or ~/.aba/bin). Install it first.")
        return 2
    if not token:
        print("✗ no CLAUDE_CODE_OAUTH_TOKEN (env or ~/.aba/config.env).")
        return 2

    work = Path(tempfile.mkdtemp(prefix="aba_live_repair_"))
    aba_home = work / "aba"; aba_home.mkdir()
    cfg_dir = work / "claude-cfg"; cfg_dir.mkdir()

    # A stand-in "micromamba" that is QUARANTINED (Gatekeeper flag set).
    binp = aba_home / "bin" / "micromamba"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\necho 'micromamba ran OK'\n")
    binp.chmod(0o755)
    subprocess.run(["xattr", "-w", "com.apple.quarantine",
                    "0081;00000000;Safari;", str(binp)], check=True)

    # The step fails while the quarantine flag is present; succeeds once cleared.
    cmd = (f'if xattr -p com.apple.quarantine "{binp}" >/dev/null 2>&1; then '
           f'echo "binary is quarantined: {binp}" >&2; exit 1; else "{binp}"; fi')
    step = Step(id="install-micromamba", title="Install micromamba", why="demo",
                commands=[cmd], timeout_seconds=120)

    # Isolate claude from the real ~/.claude; authenticate via the token.
    os.environ["CLAUDE_CONFIG_DIR"] = str(cfg_dir)
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    os.environ["ABA_HOME"] = str(aba_home)

    before = _snapshot_dotclaude()
    print(f"• claude: {claude}")
    print(f"• isolated CLAUDE_CONFIG_DIR: {cfg_dir}")
    print(f"• quarantined binary: {binp}")
    print("• starting — watch the agent's actions:\n")

    def on_event(name, payload):
        if name == "repair":
            print(f"   [agent] {payload.get('message','')}")
        elif name == "command_output" and payload.get("line"):
            print(f"   [cmd] {payload['line']}")
        elif name == "step_end":
            print(f"   [step] {payload.get('step_id')} ok={payload.get('ok')}")

    hook = make_repair_hook(cwd=str(aba_home), on_event=on_event)  # real claude, streaming
    results = Executor(Playbook(steps=[step]), on_event=on_event,
                       on_step_failed=hook, max_repair_attempts=2).run_all()

    after = _snapshot_dotclaude()
    ok = bool(results) and results[-1].ok
    still_quarantined = subprocess.run(
        ["xattr", "-p", "com.apple.quarantine", str(binp)],
        capture_output=True).returncode == 0

    print("\n── result ──")
    print(f"  step passed after repair : {ok}")
    print(f"  quarantine flag removed  : {not still_quarantined}")
    print(f"  ~/.claude untouched      : {before == after}")
    if before != after:
        print("  ⚠ WARNING: ~/.claude CHANGED — isolation failed; investigate before re-running.")
    verdict = ok and not still_quarantined and before == after
    print(f"\n{'✓ LIVE REPAIR PASSED' if verdict else '✗ LIVE REPAIR FAILED'}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
