"""
End-to-end harness for ABA.

Boots:
  - FastAPI backend in fake mode (ABA_FAKE_SESSION → scripted assistant turns)
  - Vite dev server (proxies /api → backend:8000 via vite.config.ts)
  - Headless Chromium via Playwright

Drives a small user flow and takes screenshots at each step into
tests/e2e/screenshots/, so a Read on those PNGs closes the dev loop.

Costs zero Anthropic API tokens — the fake-mode seam in backend/llm.py
replays scripted turns through the real SSE pipeline. Real tools (e.g.
list_data_files) still execute.

Run:
    .venv/bin/python tests/e2e/run.py
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
SHOT_DIR = ROOT / "tests/e2e/screenshots"
FIXTURE = ROOT / "tests/fixtures/list_files.jsonl"
BACKEND_PORT = 8000  # matches frontend/vite.config.ts proxy target
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
        print(f"ERROR: port {BACKEND_PORT} is in use. Stop the dev backend first.",
              file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="aba_e2e_"))
    db_path = work / "e2e.db"
    artifacts_dir = work / "artifacts"
    artifacts_dir.mkdir()

    # Shim that monkey-patches db.DB_PATH on import via usercustomize.
    shim_dir = work / "shim"
    shim_dir.mkdir()
    (shim_dir / "usercustomize.py").write_text(
        "import os\n"
        "_override = os.environ.get('ABA_DB_PATH_OVERRIDE')\n"
        "if _override:\n"
        "    try:\n"
        "        import db, pathlib\n"
        "        db.DB_PATH = pathlib.Path(_override)\n"
        "    except Exception:\n"
        "        pass\n"
    )

    backend_env = {
        **os.environ,
        "ABA_FAKE_SESSION": str(FIXTURE),
        "ARTIFACTS_DIR": str(artifacts_dir),
        "DATA_DIR": str(ROOT / "backend/data"),
        "ABA_DB_PATH_OVERRIDE": str(db_path),
        "PYTHONPATH": str(shim_dir) + os.pathsep + str(ROOT / "backend"),
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
    )

    procs = [backend_proc, frontend_proc]
    rc = 1
    try:
        wait_for(f"http://127.0.0.1:{BACKEND_PORT}/api/health", name="backend")
        wait_for(f"http://127.0.0.1:{frontend_port}/", name="vite")
        print(f"backend on :{BACKEND_PORT}   frontend on :{frontend_port}")
        rc = drive_browser(frontend_port)
    except Exception as e:
        print(f"\nHARNESS ERROR: {e}", file=sys.stderr)
        print(f"backend.log tail:\n{Path(work, 'backend.log').read_text()[-2000:]}",
              file=sys.stderr)
        print(f"frontend.log tail:\n{Path(work, 'frontend.log').read_text()[-2000:]}",
              file=sys.stderr)
    finally:
        print(f"logs in {work}")
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        backend_log.close()
        frontend_log.close()

    return rc


def drive_browser(frontend_port: int) -> int:
    from playwright.sync_api import sync_playwright

    url = f"http://127.0.0.1:{frontend_port}/"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        page.goto(url, wait_until="networkidle")
        page.screenshot(path=str(SHOT_DIR / "01_initial.png"), full_page=True)
        print("✓ initial page loaded")

        composer = page.locator(".composer__input")
        composer.wait_for(state="visible", timeout=5000)
        composer.fill("what files do we have?")
        page.screenshot(path=str(SHOT_DIR / "02_typed.png"), full_page=True)
        composer.press("Enter")

        # Fixture's second assistant turn includes this exact phrase.
        page.wait_for_selector("text=Found 2 CSV files", timeout=15000)
        page.screenshot(path=str(SHOT_DIR / "03_after_reply.png"), full_page=True)
        print("✓ scripted reply rendered")

        tool_done = page.locator(".msg-tool-indicator.done")
        assert tool_done.count() >= 1, "no tool_result indicator in DOM"
        print(f"✓ {tool_done.count()} tool_result indicator(s)")

        assert page.locator(".msg--user").count() == 1
        print("✓ user message rendered")

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
