"""
Phase-1 e2e: demonstrate the focus-driven context mechanic end-to-end.

Steps:
  1. Upload cells.csv via the API. Dataset entity appears in the tree.
  2. Click the dataset in the tree → focus chip updates, FocusCanvas shows metadata.
  3. Type "plot mt_fraction" → scripted Guide turn produces a histogram via
     run_python. The figure auto-registers as an entity, appears in the tree.
  4. Click the new figure in the tree → focus chip updates, FocusCanvas
     shows the image.
  5. Type a follow-up question → scripted turn 2 fires. Verify the user's
     message and the focus-aware reply both render.

Screenshots at each step; this script reuses tests/e2e/run.py's server
bootstrap by importing its helpers.

Usage:
    .venv/bin/python tests/e2e/phase1.py
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase1"
FIXTURE = ROOT / "tests/fixtures/phase1_focus.jsonl"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def wait_for(url: str, timeout: float = 30.0, name: str = "") -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.25)
    raise RuntimeError(f"{name or url} did not become ready in {timeout}s ({last_err})")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def port_free(port: int) -> bool:
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for p in SHOT_DIR.glob("*.png"):
        p.unlink()

    if not port_free(BACKEND_PORT):
        print(f"ERROR: port {BACKEND_PORT} is in use.", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="aba_phase1_"))
    db_path = work / "e2e.db"
    artifacts_dir = work / "artifacts"
    artifacts_dir.mkdir()
    data_dir = work / "data"
    data_dir.mkdir()
    # Copy the canonical sample CSV so we know the dataset content.
    src_csv = ROOT / "backend/data/cells.csv"
    (data_dir / "cells.csv").write_bytes(src_csv.read_bytes())

    backend_env = {
        **os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts_dir),
        "DATA_DIR": str(data_dir),
        "ABA_DB_PATH": str(db_path),
    }

    venv_python = ROOT / ".venv/bin/python"
    backend_log = open(work / "backend.log", "w")
    backend_proc = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(BACKEND_PORT), "--log-level", "warning"],
        cwd=str(ROOT / "backend"),
        env=backend_env,
        stdout=backend_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    frontend_port = free_port()
    frontend_env = {**os.environ, "PATH": NODE_BIN + os.pathsep + os.environ.get("PATH", "")}
    frontend_log = open(work / "frontend.log", "w")
    frontend_proc = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1",
         "--port", str(frontend_port), "--strictPort"],
        cwd=str(ROOT / "frontend"),
        env=frontend_env,
        stdout=frontend_log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    rc = 1
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", name="backend")
        wait_for(f"http://127.0.0.1:{frontend_port}/", name="vite")
        print(f"backend :{BACKEND_PORT}   frontend :{frontend_port}")
        rc = drive_browser(frontend_port)
    except Exception as e:
        print(f"\nHARNESS ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-2000:], file=sys.stderr)
        print(Path(work, "frontend.log").read_text()[-2000:], file=sys.stderr)
    finally:
        print(f"logs in {work}")
        for proc in (frontend_proc, backend_proc):
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        for proc in (frontend_proc, backend_proc):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        backend_log.close()
        frontend_log.close()

    return rc


def drive_browser(frontend_port: int) -> int:
    from playwright.sync_api import sync_playwright

    url = f"http://127.0.0.1:{frontend_port}/"
    api = f"http://127.0.0.1:{BACKEND_PORT}/api"

    # Step 1: upload a CSV through the API directly. (UI upload widget is a
    # later polish item; the API path is what matters for the mechanic.)
    src = ROOT / "backend/data/cells.csv"
    boundary = "ababoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="cells.csv"\r\n'
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{api}/upload", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        import json
        dataset = json.loads(r.read())
    print(f"✓ uploaded dataset {dataset['id']}")
    dataset_id = dataset["id"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        page.goto(url, wait_until="networkidle")
        page.screenshot(path=str(SHOT_DIR / "01_initial.png"), full_page=True)
        print("✓ page loaded — workspace focused, tree shows the uploaded dataset")

        # Step 2: click the dataset row in the tree.
        ds_row = page.locator(f'[data-entity-id="{dataset_id}"]')
        ds_row.wait_for(state="visible", timeout=5000)
        ds_row.click()
        page.wait_for_function(
            "() => document.querySelector('.focus-chip')?.classList.contains('focus-chip--active')",
            timeout=2000,
        )
        page.screenshot(path=str(SHOT_DIR / "02_dataset_focused.png"), full_page=True)
        print("✓ dataset focused — chat scope changed, focus canvas shows metadata")

        # Step 3: ask for the histogram. The fake fixture's turn 1 produces it.
        composer = page.locator(".composer__input")
        composer.fill("plot the mt_fraction distribution")
        composer.press("Enter")
        # Wait for the figure entity to appear in the tree (auto-registered).
        page.wait_for_selector('[data-entity-type="figure"]', timeout=15000)
        # Wait for streaming to finish (composer becomes enabled again).
        composer.wait_for(state="visible", timeout=5000)
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=10000,
        )
        page.screenshot(path=str(SHOT_DIR / "03_after_run.png"), full_page=True)
        print("✓ run_python produced a figure → auto-registered → tree updated")

        # Step 4: click the new figure.
        fig_row = page.locator('[data-entity-type="figure"]').first
        fig_row.click()
        page.wait_for_selector(".focus__figure", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "04_figure_focused.png"), full_page=True)
        print("✓ figure focused — image rendered in focus canvas")

        # Step 5: ask a contextual follow-up. Turn 2 of the fixture answers.
        composer.fill("what's the second peak?")
        composer.press("Enter")
        page.wait_for_selector("text=second mode", timeout=20000)
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=10000,
        )
        page.screenshot(path=str(SHOT_DIR / "05_followup.png"), full_page=True)
        print("✓ focus-aware reply rendered")

        # The figure thread holds only its own follow-up message; the first
        # user message belongs to the dataset thread.
        assert page.locator(".msg--user").count() == 1, \
            "figure thread should have exactly 1 user message"
        assert page.locator('[data-entity-type="figure"]').count() == 1
        assert page.locator(".focus__figure").count() == 1, "no focus image"

        # Step 6: switch back to the dataset → its own thread should reappear.
        ds_row.click()
        page.wait_for_selector("text=plot the mt_fraction distribution", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "06_switched_back.png"), full_page=True)
        print("✓ dataset thread persisted — switched back, first message visible")
        assert page.locator(".msg--user").count() == 1, \
            "dataset thread should have its own 1 user message after switchback"

        browser.close()

    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}  ({shot.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
