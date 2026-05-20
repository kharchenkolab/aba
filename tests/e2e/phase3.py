"""
Phase 3 e2e: result chains. Promote a figure to a result, then lift it to
a finding, then to a claim. Verify the focus canvas shows the evidence
chain at each level, and that clicking a chain row drills down.
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
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase3"
FIXTURE = ROOT / "tests/fixtures/phase1_focus.jsonl"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def wait_for(url: str, timeout: float = 30.0, name: str = ""):
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
        except Exception as e:
            last = e
            time.sleep(0.25)
    raise RuntimeError(f"{name} not ready ({last})")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


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
        print(f"port {BACKEND_PORT} in use", file=sys.stderr); return 2

    work = Path(tempfile.mkdtemp(prefix="aba_phase3_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", data / "cells.csv")

    backend_env = {
        **os.environ,
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
        print(Path(work, "backend.log").read_text()[-2000:], file=sys.stderr)
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

    # Upload + generate a figure via the fake fixture.
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
        ctx = browser.new_context(viewport={"width": 1500, "height": 900})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{frontend_port}/", wait_until="networkidle")

        # Focus the dataset and ask for a figure (fixture turn 1).
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

        # Focus the figure and promote to result.
        page.locator('[data-entity-type="figure"]').first.click()
        page.wait_for_selector(".focus__figure", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "01_figure_focused.png"), full_page=True)

        page.locator(".focus__promote").click()
        page.wait_for_selector(".promote-dialog", timeout=2000)
        page.locator(".promote-dialog__textarea").fill(
            "Sample S4 has elevated mt_fraction (~0.13), well above the other samples — likely doublet contamination.",
        )
        page.screenshot(path=str(SHOT_DIR / "02_promote_dialog.png"), full_page=True)
        page.locator(".promote-dialog__btn--primary").click()
        # After save, focus shifts to the new result and the dialog closes.
        page.wait_for_selector(".focus__type--result", timeout=5000)
        page.wait_for_selector(".focus__chain", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "03_result_focused.png"), full_page=True)
        print("✓ figure promoted to result; evidence chain visible")

        # Lift the result to a finding.
        page.locator(".focus__promote").click()
        page.wait_for_selector(".promote-dialog", timeout=2000)
        page.locator(".promote-dialog__textarea").fill(
            "Sample-level QC consistently flags donor S4 across multiple per-cell metrics.",
        )
        page.locator(".promote-dialog__btn--primary").click()
        page.wait_for_selector(".focus__type--finding", timeout=5000)
        page.wait_for_selector(".focus__chain", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "04_finding_focused.png"), full_page=True)
        print("✓ result lifted to finding; supporting results visible")

        # Lift the finding to a claim.
        page.locator(".focus__promote").click()
        page.wait_for_selector(".promote-dialog", timeout=2000)
        page.locator(".promote-dialog__textarea").fill(
            "Sample S4 must be excluded from downstream analysis due to consistent QC failures.",
        )
        page.locator(".promote-dialog__btn--primary").click()
        page.wait_for_selector(".focus__type--claim", timeout=5000)
        page.wait_for_selector(".focus__chain", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "05_claim_focused.png"), full_page=True)
        print("✓ finding lifted to claim; chain complete")

        # Drill-down: click the chain row to focus back on the finding.
        page.locator(".focus__chain-row").first.click()
        page.wait_for_selector(".focus__type--finding", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "06_drill_back.png"), full_page=True)
        print("✓ chain row click drills back to the finding")

        # Tree should now have all four entities.
        for t in ("result", "finding", "claim"):
            count = page.locator(f'[data-entity-type="{t}"]').count()
            assert count >= 1, f"expected {t} in tree"
        print("✓ tree shows result, finding, claim sections")

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
