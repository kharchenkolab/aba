"""Tier-1 deterministic UI audits (eval Stage 2).

Zero-token: boots the app in fake mode + isolated multi-project mode
(ABA_PROJECTS_DIR → temp), drives each registered state in headless Chromium,
and runs no-LLM checks (contrast, …). Prints a per-state scorecard.

Reuses the tests/e2e boot pattern. Backend must own port 8000 (the vite proxy
target), so stop the dev backend before running, e.g.:

    .venv/bin/python eval/audits/harness.py

Run a subset:  --states home-empty,skills-panel   --checks contrast
"""
from __future__ import annotations
import argparse
import os
import shutil
from types import SimpleNamespace
import signal
import socket
import subprocess
import sys
import time
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = Path(__file__).resolve().parent
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"
BACKEND_PORT = 8000
FIXTURE = ROOT / "tests/fixtures/phase1_focus.jsonl"

sys.path.insert(0, str(AUDIT_DIR))
import checks as checks_pkg          # noqa: E402
import report as report_mod          # noqa: E402
from states import STATES            # noqa: E402


def wait_for(url: str, timeout: float = 30.0, name: str = "") -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"{name or url} not ready in {timeout}s ({last})")


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def port_free(port: int) -> bool:
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port)); return True
    except OSError:
        return False
    finally:
        s.close()


def boot(work: Path):
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", data / "cells.csv")
    projects_dir = work / "projects"; projects_dir.mkdir()

    env = {
        **os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts),
        "DATA_DIR": str(data),
        "ABA_PROJECTS_DIR": str(projects_dir),   # isolated multi-project; no ABA_DB_PATH
    }
    venv_python = ROOT / ".venv/bin/python"
    backend_log = open(work / "backend.log", "w")
    backend = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(BACKEND_PORT), "--log-level", "warning"],
        cwd=str(ROOT / "backend"), env=env,
        stdout=backend_log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    fport = free_port()
    frontend_log = open(work / "frontend.log", "w")
    frontend = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1",
         "--port", str(fport), "--strictPort"],
        cwd=str(ROOT / "frontend"),
        env={**os.environ, "PATH": NODE_BIN + os.pathsep + os.environ.get("PATH", "")},
        stdout=frontend_log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    return fport, [frontend, backend], [backend_log, frontend_log]


def teardown(procs, logs):
    for p in procs:
        try: os.killpg(p.pid, signal.SIGINT)
        except ProcessLookupError: pass
    for p in procs:
        try: p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
    for f in logs:
        f.close()


def run(selected_states, selected_checks, update_baseline=False, full=True) -> int:
    if not port_free(BACKEND_PORT):
        print(f"port {BACKEND_PORT} in use — stop the dev backend first", file=sys.stderr)
        return 2

    active_checks = [c for c in checks_pkg.ALL if c.NAME in selected_checks]
    states = [s for s in STATES if s.name in selected_states]
    work = Path(tempfile.mkdtemp(prefix="aba_audit_"))
    shots = work / "shots"; shots.mkdir()

    fport, procs, logs = boot(work)
    report: dict = {}
    rc = 0
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", name="backend")
        wait_for(f"http://127.0.0.1:{fport}/", name="vite")
        ctx = SimpleNamespace(
            base_url=f"http://127.0.0.1:{fport}",
            api=f"http://127.0.0.1:{BACKEND_PORT}/api",
            projects_dir=work / "projects",
        )
        print(f"backend :{BACKEND_PORT}  frontend :{fport}  ({len(states)} states × {len(active_checks)} checks)\n")

        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            for st in states:
                page = browser.new_context(viewport={"width": 1440, "height": 900}).new_page()
                state_findings: dict = {}
                try:
                    st.setup(page, ctx)
                    page.screenshot(path=str(shots / f"{st.name}.png"), full_page=True)
                    for chk in active_checks:
                        findings = chk.run(page, st)
                        state_findings[chk.NAME] = findings
                except Exception as e:  # noqa: BLE001
                    state_findings["_error"] = str(e)
                report[st.name] = state_findings
                page.context.close()
                _print_state(st.name, state_findings)
            browser.close()
    except Exception as e:  # noqa: BLE001
        print(f"HARNESS ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-1500:], file=sys.stderr)
        rc = 1
    finally:
        teardown(procs, logs)
        print(f"\nlogs + screenshots in {work}")

    if rc:
        return rc

    total = sum(len(f.get(c.NAME, [])) for f in report.values() for c in active_checks)
    out = report_mod.write_run(report, shots)
    print(f"\n=== {total} finding(s) across {len(report)} state(s) — {out} ===")

    # Baseline gate only makes sense over a full run (a subset can't see the
    # whole accepted set, so it would mis-flag "fixed").
    if update_baseline:
        n = report_mod.write_baseline(report)
        print(f"baseline updated — {n} accepted signature(s)")
        return 0
    if not full:
        print("(subset run — baseline gate skipped)")
        return 0

    regressions, fixed = report_mod.gate(report)
    if fixed:
        print(f"\n{len(fixed)} baseline finding(s) no longer present (fixed?):")
        for s in sorted(fixed)[:12]:
            print(f"  - {s[0]} / {s[1]} / {s[2]}")
    if regressions:
        print(f"\n✗ {len(regressions)} NEW finding(s) beyond baseline (regressions):")
        for s in sorted(regressions):
            print(f"  + {s[0]} / {s[1]} / {s[2]}")
        return 1
    print("\n✓ no regressions vs baseline")
    return 0


def _print_state(name: str, findings: dict) -> None:
    if "_error" in findings:
        print(f"  ✗ {name}: SETUP ERROR — {findings['_error']}")
        return
    counts = {k: len(v) for k, v in findings.items()}
    n = sum(counts.values())
    mark = "✓" if n == 0 else "✗"
    summary = ", ".join(f"{k}={v}" for k, v in counts.items())
    print(f"  {mark} {name}: {summary}")
    for chk, items in findings.items():
        for it in items[:8]:
            if chk == "contrast":
                print(f"      [contrast] {it['selector']}  {it.get('ratio')}:1 "
                      f"(need {it.get('expected')})  fg={it.get('fg')} bg={it.get('bg')}")
            elif chk == "tap_target":
                print(f"      [tap_target/{it.get('severity')}] {it['selector']} "
                      f"{it['w']}×{it['h']}  '{it.get('title')}'")
            else:
                print(f"      [{chk}] {it}")


def main() -> int:
    ap = argparse.ArgumentParser()
    all_states = ",".join(s.name for s in STATES)
    all_checks = ",".join(c.NAME for c in checks_pkg.ALL)
    ap.add_argument("--states", default=all_states)
    ap.add_argument("--checks", default=all_checks)
    ap.add_argument("--update-baseline", action="store_true",
                    help="write the current findings as the accepted baseline")
    args = ap.parse_args()
    full = args.states == all_states and args.checks == all_checks
    return run(set(args.states.split(",")), set(args.checks.split(",")),
               update_baseline=args.update_baseline, full=full)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
