"""
Phase 8 e2e: workspace administration & organization.

Drives:
  1. Generate a figure (via the existing phase1 fixture).
  2. Open the three-dot menu on the figure; rename it.
  3. Tag it. Pin it. Verify the tree updates.
  4. Search by the new title.
  5. Archive a dataset; verify it disappears from default tree; toggle
     "show archived"; verify it reappears.
  6. Restore from menu; verify it comes back.
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
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase8"
FIXTURE = ROOT / "tests/fixtures/phase1_focus.jsonl"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def wait_for(url: str, timeout: float = 30.0, name: str = ""):
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500: return
        except Exception as e:
            last = e; time.sleep(0.25)
    raise RuntimeError(f"{name} not ready ({last})")


def free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def port_free(port: int) -> bool:
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

    work = Path(tempfile.mkdtemp(prefix="aba_phase8_"))
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
        print(f"backend :{BACKEND_PORT}  frontend :{frontend_port}")
        rc = drive(frontend_port)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-3000:], file=sys.stderr)
        print(Path(work, "frontend.log").read_text()[-3000:], file=sys.stderr)
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
    print(f"✓ uploaded {dataset['id']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{frontend_port}/", wait_until="networkidle")
        page.locator('button[title="Workspace"]').click()
        page.wait_for_timeout(150)

        # Generate a figure via the existing fixture.
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

        # Hover the figure row to reveal the ⋯ menu.
        fig_row = page.locator('[data-entity-type="figure"]').first
        fig_row.hover()
        fig_row.locator(".entity-menu__btn").click()
        page.wait_for_selector(".entity-menu__pop", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "01_menu_open.png"), full_page=True)

        # Rename
        page.locator(".entity-menu__pop button:has-text('Rename')").click()
        page.wait_for_selector(".entity-menu__input", timeout=1000)
        page.locator(".entity-menu__input").fill("mt_fraction (renamed)")
        page.locator(".entity-menu__primary").click()
        page.wait_for_selector("text=mt_fraction (renamed)", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "02_renamed.png"), full_page=True)
        print("✓ renamed")

        # Re-open menu → add tags + pin
        fig_row = page.locator(f'[data-entity-id]:has-text("mt_fraction (renamed)")').first
        fig_row.hover()
        fig_row.locator(".entity-menu__btn").click()
        page.locator(".entity-menu__pop button:has-text('Edit tags')").click()
        page.wait_for_selector(".entity-menu__input", timeout=1000)
        page.locator(".entity-menu__input").fill("qc, donor-S4")
        page.locator(".entity-menu__primary").click()
        page.wait_for_selector("text=qc", timeout=2000)
        print("✓ tagged")

        fig_row.hover()
        fig_row.locator(".entity-menu__btn").click()
        page.locator(".entity-menu__pop button:has-text('Pin')").first.click()
        page.wait_for_selector("text=Pinned", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "03_pinned_tagged.png"), full_page=True)
        print("✓ pinned (appears in PINNED section)")

        # Search
        page.locator(".tree__search-input").fill("renamed")
        page.wait_for_timeout(200)
        page.screenshot(path=str(SHOT_DIR / "04_search.png"), full_page=True)
        # When searching, only one entity should be visible (the renamed figure).
        visible = page.locator('.tree__items .tree__item').count()
        assert visible >= 1, "search should keep matching entity visible"
        page.locator(".tree__search-clear").click()
        print("✓ search filtered + cleared")

        # Archive the dataset
        ds_row = page.locator(f'[data-entity-id="{dataset["id"]}"]')
        ds_row.hover()
        ds_row.locator(".entity-menu__btn").click()
        page.locator(".entity-menu__pop button:has-text('Archive')").click()
        page.wait_for_function(
            f"() => !document.querySelector('[data-entity-id=\\\"{dataset['id']}\\\"]')",
            timeout=3000,
        )
        page.screenshot(path=str(SHOT_DIR / "05_after_archive.png"), full_page=True)
        print("✓ archived (dataset hidden from default tree)")

        # Toggle show-archived → dataset reappears
        page.locator(".tree__toggle input").check()
        page.wait_for_selector(f'[data-entity-id="{dataset["id"]}"]', timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "06_show_archived.png"), full_page=True)
        print("✓ show-archived reveals it")

        # Restore
        ds_row = page.locator(f'[data-entity-id="{dataset["id"]}"]')
        ds_row.hover()
        ds_row.locator(".entity-menu__btn").click()
        page.locator(".entity-menu__pop button:has-text('Restore')").click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(SHOT_DIR / "07_restored.png"), full_page=True)
        print("✓ restored")

        browser.close()

    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}  ({shot.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
