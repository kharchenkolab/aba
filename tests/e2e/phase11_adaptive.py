"""
Phase 11 e2e: adaptive context (§3.6) — passive instrumentation, end-of-session
reflection, Settings page review.

In fake mode the reflection placeholder fires once the tool-call threshold is
exceeded. To make this trigger on a small fixture we lower the threshold to 1.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase11"
FIXTURE = ROOT / "tests/fixtures/phase1_focus.jsonl"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def wait_for(url, timeout=30.0, name=""):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500: return
        except Exception as e:
            last = e; time.sleep(0.25)
    raise RuntimeError(f"{name} not ready ({last})")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def port_free(port):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: s.bind(("127.0.0.1", port)); return True
    except OSError: return False
    finally: s.close()


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for p in SHOT_DIR.glob("*.png"): p.unlink()
    if not port_free(BACKEND_PORT):
        print(f"port {BACKEND_PORT} in use", file=sys.stderr); return 2

    work = Path(tempfile.mkdtemp(prefix="aba_phase11_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", data / "cells.csv")

    backend_env = {**os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts),
        "DATA_DIR": str(data),
        "ABA_DB_PATH": str(work / "e2e.db"),
        # Force the reflection to fire on small fixtures by lowering threshold.
        "PYTHONSTARTUP": str(work / "lower_threshold.py"),
    }
    # Lower the threshold via a startup hook so the backend reflects on any
    # session, even those with one tool call.
    shim = work / "lower_threshold.py"
    shim.write_text(
        "try:\n"
        "    import adaptive\n"
        "    adaptive.REFLECTION_TOOL_CALL_THRESHOLD = 1\n"
        "except Exception:\n"
        "    pass\n"
    )
    # PYTHONSTARTUP only fires for interactive mode; use a sitecustomize.py
    # on PYTHONPATH instead.
    shim_dir = work / "shim"; shim_dir.mkdir()
    # sitecustomize is loaded by Python's `site` module on startup whenever
    # it's on sys.path — more reliable than usercustomize for our purpose.
    (shim_dir / "sitecustomize.py").write_text(
        "import builtins\n"
        "_orig_import = builtins.__import__\n"
        "def _hook(name, *a, **k):\n"
        "    mod = _orig_import(name, *a, **k)\n"
        "    if name == 'adaptive':\n"
        "        try:\n"
        "            mod.REFLECTION_TOOL_CALL_THRESHOLD = 1\n"
        "        except Exception:\n"
        "            pass\n"
        "    return mod\n"
        "builtins.__import__ = _hook\n"
    )
    backend_env.pop("PYTHONSTARTUP", None)
    backend_env["PYTHONPATH"] = str(shim_dir) + os.pathsep + str(ROOT / "backend")

    venv_python = ROOT / ".venv/bin/python"
    backend_log = open(work / "backend.log", "w")
    backend_proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT), "--log-level", "warning"],
        cwd=str(ROOT / "backend"), env=backend_env,
        stdout=backend_log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    frontend_port = free_port()
    frontend_log = open(work / "frontend.log", "w")
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1",
         "--port", str(frontend_port), "--strictPort"],
        cwd=str(ROOT / "frontend"),
        env={**os.environ, "PATH": NODE_BIN + os.pathsep + os.environ.get("PATH", "")},
        stdout=frontend_log, stderr=subprocess.STDOUT, start_new_session=True,
    )
    rc = 1
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", name="backend")
        wait_for(f"http://127.0.0.1:{frontend_port}/", name="vite")
        rc = drive(frontend_port)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-3000:], file=sys.stderr)
    finally:
        print(f"logs in {work}")
        for proc in (frontend_proc, backend_proc):
            try: os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError: pass
        for proc in (frontend_proc, backend_proc):
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
        backend_log.close(); frontend_log.close()
    return rc


def drive(frontend_port: int) -> int:
    from playwright.sync_api import sync_playwright

    src = ROOT / "backend/data/cells.csv"
    boundary = "ababoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="cells.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    with urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/upload", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )) as r:
        dataset = json.loads(r.read())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{frontend_port}/", wait_until="networkidle")

        # Run one session that calls a tool — the reflection fires at the end.
        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_selector(".focus__preview-table", timeout=5000)
        composer = page.locator(".composer__input")
        composer.fill("plot the mt_fraction distribution")
        composer.press("Enter")
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=30000,
        )

        # Pending count should appear as a badge on the bottom-left user avatar.
        page.wait_for_function(
            "() => document.querySelector('.rail__badge')?.textContent",
            timeout=10000,
        )
        page.screenshot(path=str(SHOT_DIR / "01_pending_badge.png"), full_page=True)
        print("✓ pending suggestion badge surfaced on the user avatar")

        # Open Settings via the user button.
        page.locator(".rail__user").click()
        page.wait_for_selector(".settings", timeout=2000)
        page.wait_for_selector(".suggestion__text", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "02_settings_open.png"), full_page=True)

        suggestion_text = page.locator(".suggestion__text").first.inner_text()
        print(f"  suggestion: {suggestion_text[:80]}…")

        # Approve.
        page.locator(".suggestion__approve").click()
        # After approval the list should clear (pending count is 0).
        page.wait_for_selector(".settings__empty", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "03_after_approve.png"), full_page=True)
        print("✓ approved → list clears, suggestion promoted to policy")

        browser.close()

    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}  ({shot.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
