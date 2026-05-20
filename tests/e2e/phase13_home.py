"""
Phase 13 e2e: Home screen.

  1. Fresh DB → Home shows the empty-state entry cards.
  2. Click "Try a sample" → a dataset registers and the workspace opens.
  3. Navigate back Home → the populated mini-dashboard shows counts +
     recent activity.
"""
from __future__ import annotations
import json, os, shutil, signal, socket, subprocess, sys, tempfile, time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase13"
FIXTURE = ROOT / "tests/fixtures/list_files.jsonl"
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
    work = Path(tempfile.mkdtemp(prefix="aba_p13_"))
    (work / "artifacts").mkdir(); (work / "data").mkdir()
    env = {**os.environ, "ABA_FAKE_SESSION": str(FIXTURE),
           "ARTIFACTS_DIR": str(work / "artifacts"), "DATA_DIR": str(work / "data"),
           "ABA_DB_PATH": str(work / "e2e.db")}
    vp = ROOT / ".venv/bin/python"
    blog = open(work / "b.log", "w")
    bp = subprocess.Popen([str(vp), "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
        "--port", str(BACKEND_PORT), "--log-level", "warning"], cwd=str(ROOT / "backend"),
        env=env, stdout=blog, stderr=subprocess.STDOUT, start_new_session=True)
    fport = free_port()
    flog = open(work / "f.log", "w")
    fp = subprocess.Popen(["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port",
        str(fport), "--strictPort"], cwd=str(ROOT / "frontend"),
        env={**os.environ, "PATH": NODE_BIN + os.pathsep + os.environ.get("PATH", "")},
        stdout=flog, stderr=subprocess.STDOUT, start_new_session=True)
    rc = 1
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health")
        wait_for(f"http://127.0.0.1:{fport}/")
        rc = drive(fport)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(Path(work, "b.log").read_text()[-2500:], file=sys.stderr)
        print(Path(work, "f.log").read_text()[-2000:], file=sys.stderr)
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
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        page = br.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto(f"http://127.0.0.1:{fport}/", wait_until="networkidle")

        # Empty-state Home
        page.wait_for_selector(".home__empty", timeout=5000)
        page.wait_for_selector(".home__card", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "01_empty.png"), full_page=True)
        print("✓ empty-state Home with entry cards")

        # Try a sample → opens workspace with a dataset focused
        page.locator(".home__card:has-text('Try a sample')").click()
        page.wait_for_selector(".focus__preview-table", timeout=10000)
        page.screenshot(path=str(SHOT_DIR / "02_sample_workspace.png"), full_page=True)
        print("✓ sample project loaded into the workspace")

        # Navigate Home → populated dashboard (the sample dataset gives it
        # a count + a recent-activity entry).
        page.locator('button[title="Home"]').click()
        page.wait_for_selector(".home__dash", timeout=5000)
        page.wait_for_selector(".home__stat", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "03_dashboard.png"), full_page=True)
        n_stats = page.locator(".home__stat").count()
        n_events = page.locator(".home__event").count()
        print(f"✓ populated dashboard: {n_stats} stat tiles, {n_events} recent events")
        assert n_stats >= 1
        br.close()
    print("\nscreenshots:")
    for s in sorted(SHOT_DIR.glob("*.png")): print(f"  {s.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
