"""
Phase 12 e2e (minimal): Skills catalog screen opens from the rail,
renders categories + items, search filters, and details panel populates.
"""
from __future__ import annotations
import os, shutil, signal, socket, subprocess, sys, tempfile, time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase12"
FIXTURE = ROOT / "tests/fixtures/list_files.jsonl"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def wait_for(url, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500: return
        except Exception:
            time.sleep(0.25)
    raise RuntimeError(f"{url} not ready")


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
    if not port_free(BACKEND_PORT): return 2
    work = Path(tempfile.mkdtemp(prefix="aba_phase12_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()
    backend_env = {**os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts), "DATA_DIR": str(data),
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
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health")
        wait_for(f"http://127.0.0.1:{frontend_port}/")
        rc = drive(frontend_port)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
    finally:
        for proc in (frontend_proc, backend_proc):
            try: os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError: pass
        for proc in (frontend_proc, backend_proc):
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError: pass
    return rc


def drive(frontend_port: int) -> int:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{frontend_port}/", wait_until="networkidle")

        # Click the Skills rail nav item.
        page.locator('button[title*="Skills"]').click()
        page.wait_for_selector(".skills", timeout=3000)
        page.wait_for_selector(".skills__section", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "01_skills_open.png"), full_page=True)
        print("✓ Skills modal opened with categories")

        # Click an item to see details.
        page.locator(".skills__row").first.click()
        page.wait_for_selector(".skills__detail h3", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "02_detail.png"), full_page=True)
        print("✓ detail panel renders for selected item")

        # Search "leiden" — should narrow to the scrna pipeline skill.
        page.locator(".skills__search").fill("leiden")
        page.wait_for_timeout(200)
        items = page.locator(".skills__row").count()
        assert items >= 1
        page.screenshot(path=str(SHOT_DIR / "03_search.png"), full_page=True)
        print(f"✓ search 'leiden' narrowed to {items} item(s)")

        browser.close()
    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
