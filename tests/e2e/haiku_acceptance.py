"""
Haiku acceptance run for Phase 1.

One live run against the real Anthropic API to verify:
  - The focus context preamble is sent (the model knows it's looking at the
    dataset without being told)
  - run_python actually executes against the real LLM's emitted code
  - Auto-registration of figures still works
  - The reply naturally references the dataset by what's in its context

Skips entirely if ANTHROPIC_API_KEY is not set. Costs a small handful of
Haiku tokens (typically < $0.005). Reserve for once-per-phase acceptance.

Usage:
    .venv/bin/python tests/e2e/haiku_acceptance.py
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
SHOT_DIR = ROOT / "tests/e2e/screenshots/haiku"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


def _env_key() -> str:
    """Read ANTHROPIC_API_KEY from .env (or actual env)."""
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return os.environ.get("ANTHROPIC_API_KEY", "")


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
    raise RuntimeError(f"{name or url} did not become ready ({last_err})")


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
    key = _env_key()
    if not key or key.startswith("sk-ant-..."):
        print("ANTHROPIC_API_KEY not set; skipping live Haiku acceptance.")
        return 0

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for p in SHOT_DIR.glob("*.png"):
        p.unlink()

    if not port_free(BACKEND_PORT):
        print(f"ERROR: port {BACKEND_PORT} in use.", file=sys.stderr)
        return 2

    work = Path(tempfile.mkdtemp(prefix="aba_haiku_"))
    artifacts_dir = work / "artifacts"
    artifacts_dir.mkdir()
    data_dir = work / "data"
    data_dir.mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", data_dir / "cells.csv")

    backend_env = {
        **os.environ,
        "ANTHROPIC_API_KEY": key,
        "ABA_MODEL": "claude-haiku-4-5-20251001",
        "ARTIFACTS_DIR": str(artifacts_dir),
        "DATA_DIR": str(data_dir),
        "ABA_DB_PATH": str(work / "test.db"),
    }
    backend_env.pop("ABA_FAKE_SESSION", None)

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
        rc = drive_live(frontend_port)
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-2000:], file=sys.stderr)
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


def drive_live(frontend_port: int) -> int:
    from playwright.sync_api import sync_playwright

    url = f"http://127.0.0.1:{frontend_port}/"
    api = f"http://127.0.0.1:{BACKEND_PORT}/api"

    # Upload cells.csv.
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
        dataset = json.loads(r.read())
    print(f"✓ uploaded {dataset['id']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.goto(url, wait_until="networkidle")

        # Focus the dataset.
        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_function(
            "() => document.querySelector('.focus-chip')?.classList.contains('focus-chip--active')",
            timeout=2000,
        )
        page.screenshot(path=str(SHOT_DIR / "01_focused.png"), full_page=True)

        # Ask a focus-aware question. We do NOT name the dataset — the model
        # should know which one we mean from the focus preamble.
        composer = page.locator(".composer__input")
        composer.fill("make a quick histogram of the mt_fraction column")
        composer.press("Enter")
        # Wait for a figure to appear in the tree.
        page.wait_for_selector('[data-entity-type="figure"]', timeout=90000)
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=120000,
        )
        page.screenshot(path=str(SHOT_DIR / "02_after_haiku.png"), full_page=True)

        # Click the new figure, ask a focus-aware follow-up.
        page.locator('[data-entity-type="figure"]').first.click()
        page.wait_for_selector(".focus__figure", timeout=3000)
        composer.fill("anything unusual in this distribution?")
        composer.press("Enter")
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=120000,
        )
        page.screenshot(path=str(SHOT_DIR / "03_followup.png"), full_page=True)

        # Toggle trace on to see the inner loop the live model produced.
        page.locator(".trace-toggle").click()
        page.wait_for_selector(".trace-card", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "04_trace_on.png"), full_page=True)

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
