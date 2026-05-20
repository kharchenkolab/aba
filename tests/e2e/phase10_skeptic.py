"""
Phase 10 e2e: the Skeptic advisor.

Promote a figure to a result. The Skeptic fires asynchronously (in fake
mode it returns a deterministic placeholder so this runs token-free).
The AdvisorRail polls the focused entity's notes endpoint and surfaces
the Skeptic's review when it lands.
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
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase10"
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

    work = Path(tempfile.mkdtemp(prefix="aba_phase10_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", data / "cells.csv")

    backend_env = {**os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts),
        "DATA_DIR": str(data),
        "ABA_DB_PATH": str(work / "e2e.db"),
    }
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
        page.locator('button[title="Workspace"]').click()
        page.wait_for_timeout(150)

        # Generate a figure via the fixture.
        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_selector(".focus__preview-table", timeout=5000)
        composer = page.locator(".composer__input")
        composer.fill("plot the mt_fraction distribution")
        composer.press("Enter")
        page.wait_for_selector('[data-entity-type="figure"]', timeout=15000)
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=10000,
        )

        # Focus the figure, promote to result.
        page.locator('[data-entity-type="figure"]').first.click()
        page.wait_for_selector(".focus__figure", timeout=3000)
        page.locator("button:has-text('Promote to result')").click()
        page.wait_for_selector(".promote-dialog", timeout=2000)
        page.locator(".promote-dialog__textarea").fill(
            "Sample S4 has elevated mt_fraction (~0.13), likely doublet contamination.",
        )
        page.locator(".promote-dialog__btn--primary").click()
        page.wait_for_selector(".focus__type--result", timeout=5000)
        page.screenshot(path=str(SHOT_DIR / "01_promoted.png"), full_page=True)

        # Wait for Skeptic's idea badge in the right-side rail, then expand it.
        page.wait_for_selector(".adv-row--has-notes", timeout=10000)
        page.locator(".adv-row--has-notes .adv-rowhead").first.click()
        page.wait_for_selector(".adv-note-text", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "02_skeptic_note.png"), full_page=True)
        print("✓ Skeptic idea badge appeared; expanded to read the note")

        note_text = page.locator(".adv-note-text").first.inner_text()
        assert "Skeptic" in note_text or "outlier" in note_text.lower()
        print(f"  note preview: {note_text[:80]}…")

        # Switch focus → notes pane re-fetches with the new entity's notes.
        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_selector(".focus__preview-table", timeout=3000)
        # Dataset has no notes yet → no .adv-row--has-notes
        page.wait_for_function(
            "() => document.querySelectorAll('.adv-row--has-notes').length === 0",
            timeout=5000,
        )
        page.screenshot(path=str(SHOT_DIR / "03_switched_to_dataset.png"), full_page=True)
        print("✓ rail clears when switching focus")

        # Switch back to the result → notes are still there.
        page.locator('[data-entity-type="result"]').first.click()
        page.wait_for_selector(".adv-row--has-notes", timeout=5000)
        page.screenshot(path=str(SHOT_DIR / "04_back_to_result.png"), full_page=True)
        print("✓ notes persist across focus changes")

        browser.close()

    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}  ({shot.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
