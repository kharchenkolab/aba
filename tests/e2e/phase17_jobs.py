"""
Phase 17 e2e: background job queue.

Scripts a Guide turn that submits a background job, then:
  1. Asserts the chat returns immediately (not blocked on the run).
  2. Opens the Queues panel from the rail; sees the job.
  3. Waits for the job to finish; the figure auto-registers in the tree.
"""
from __future__ import annotations
import json, os, shutil, signal, socket, subprocess, sys, tempfile, time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase17"
FIXTURE = ROOT / "tests/fixtures/bg_job.jsonl"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def wait_for(url, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500: return
        except Exception: time.sleep(0.25)
    raise RuntimeError(f"{url} not ready")


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def port_free(port):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try: s.bind(("127.0.0.1", port)); return True
    except OSError: return False
    finally: s.close()


def main() -> int:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for p in SHOT_DIR.glob("*.png"): p.unlink()
    if not port_free(BACKEND_PORT): return 2
    work = Path(tempfile.mkdtemp(prefix="aba_p17_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", data / "cells.csv")
    backend_env = {**os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts), "DATA_DIR": str(data),
        "ABA_DB_PATH": str(work / "e2e.db")}
    venv_python = ROOT / ".venv/bin/python"
    blog = open(work / "backend.log", "w")
    bp = subprocess.Popen(
        [str(venv_python), "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(BACKEND_PORT), "--log-level", "warning"],
        cwd=str(ROOT / "backend"), env=backend_env,
        stdout=blog, stderr=subprocess.STDOUT, start_new_session=True)
    fport = free_port()
    flog = open(work / "frontend.log", "w")
    fp = subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", str(fport), "--strictPort"],
        cwd=str(ROOT / "frontend"),
        env={**os.environ, "PATH": NODE_BIN + os.pathsep + os.environ.get("PATH", "")},
        stdout=flog, stderr=subprocess.STDOUT, start_new_session=True)
    rc = 1
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health")
        wait_for(f"http://127.0.0.1:{fport}/")
        rc = drive(fport)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-3000:], file=sys.stderr)
    finally:
        for proc in (fp, bp):
            try: os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError: pass
        for proc in (fp, bp):
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
    return rc


def drive(fport: int) -> int:
    from playwright.sync_api import sync_playwright
    src = ROOT / "backend/data/cells.csv"
    boundary = "ababoundary"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"cells.csv\"\r\nContent-Type: text/csv\r\n\r\n").encode() \
        + src.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    with urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/upload", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})) as r:
        dataset = json.loads(r.read())

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{fport}/", wait_until="networkidle")
        page.locator('button[title="Project"]').click()
        page.wait_for_timeout(150)

        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_selector(".focus__preview-table", timeout=5000)
        composer = page.locator(".composer__input")
        composer.fill("run the mt_fraction histogram in the background")
        composer.press("Enter")
        # Chat returns promptly — the closing summary mentions the background job.
        page.wait_for_selector("text=background job", timeout=15000)
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled", timeout=15000)
        # The Queues rail badge should appear (1 active job) — but the job may
        # finish fast. Open Queues regardless.
        page.screenshot(path=str(SHOT_DIR / "01_after_submit.png"), full_page=True)

        page.locator('button[title*="Queues"]').click()
        page.wait_for_selector(".queues", timeout=3000)
        page.wait_for_selector(".job", timeout=5000)
        page.screenshot(path=str(SHOT_DIR / "02_queues.png"), full_page=True)
        print("✓ job appears in the Queues panel")

        # Wait for the job to reach 'Done'.
        page.wait_for_selector(".job .q--done", timeout=30000)
        page.screenshot(path=str(SHOT_DIR / "03_done.png"), full_page=True)
        print("✓ job completed")

        # Close Queues; the figure should now be in the tree (onJobsChanged
        # refreshed it).
        page.locator(".queues__close").click()
        page.wait_for_selector('[data-entity-type="figure"]', timeout=10000)
        page.screenshot(path=str(SHOT_DIR / "04_figure_registered.png"), full_page=True)
        print("✓ background job's figure registered in the tree")

        browser.close()
    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
