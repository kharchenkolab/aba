"""
Phase 7: real pbmc3k story end-to-end.

Skips silently if ANTHROPIC_API_KEY isn't set. Downloads ~8MB from 10x
Genomics, runs the full scRNA-seq pipeline via Haiku, screenshots each
key moment. Costs single-digit cents per run.

Set PBMC3K_URL to override the source (e.g. point at a local file://
during testing). Default: the canonical 10x sample.
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
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase7"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"
PBMC3K_URL = os.environ.get(
    "PBMC3K_URL",
    "https://cf.10xgenomics.com/samples/cell/pbmc3k/pbmc3k_filtered_gene_bc_matrices.tar.gz",
)


def _env_key() -> str:
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return os.environ.get("ANTHROPIC_API_KEY", "")


def wait_for(url: str, timeout: float = 30.0, name: str = ""):
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return
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
    key = _env_key()
    if not key or key.startswith("sk-ant-..."):
        print("ANTHROPIC_API_KEY not set; skipping Phase 7 live acceptance.")
        return 0

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for p in SHOT_DIR.glob("*.png"): p.unlink()
    if not port_free(BACKEND_PORT):
        print(f"port {BACKEND_PORT} in use", file=sys.stderr); return 2

    work = Path(tempfile.mkdtemp(prefix="aba_phase7_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()

    backend_env = {**os.environ,
        "ANTHROPIC_API_KEY": key,
        "ABA_MODEL": "claude-haiku-4-5-20251001",
        "ARTIFACTS_DIR": str(artifacts),
        "DATA_DIR": str(data),
        "ABA_DB_PATH": str(work / "e2e.db"),
    }
    backend_env.pop("ABA_FAKE_SESSION", None)
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

    print(f"downloading: {PBMC3K_URL}")
    t0 = time.time()
    body = json.dumps({"url": PBMC3K_URL}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/upload-url",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        dataset = json.loads(r.read())
    print(f"✓ downloaded + registered as {dataset['id']} ({time.time() - t0:.1f}s)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{frontend_port}/", wait_until="networkidle")

        # Focus the dataset.
        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_function(
            "() => document.querySelector('.focus-chip').textContent.includes('dataset')",
            timeout=3000,
        )
        page.screenshot(path=str(SHOT_DIR / "01_archive_focused.png"), full_page=True)

        # The headline ask.
        composer = page.locator(".composer__input")
        composer.fill(
            "this is the 10x pbmc3k sample — inspect it, then run a compact "
            "scanpy QC + clustering pipeline (filter, normalize, HVG, PCA, "
            "neighbors, UMAP, leiden). Use timeout_s=300 for long steps."
        )
        composer.press("Enter")
        # Generous: real pipeline on 2700 cells × 32k genes.
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=600000,
        )
        page.screenshot(path=str(SHOT_DIR / "02_pipeline_complete.png"), full_page=True)

        n_figs = page.locator('[data-entity-type="figure"]').count()
        print(f"✓ pipeline registered {n_figs} figure(s)")

        # Click the last figure (most likely the UMAP).
        page.locator('[data-entity-type="figure"]').last.click()
        page.wait_for_selector(".focus__figure", timeout=5000)
        page.screenshot(path=str(SHOT_DIR / "03_last_figure.png"), full_page=True)

        # Trace on to see the inner loop.
        page.locator(".trace-toggle").click()
        page.wait_for_selector(".trace-card", timeout=5000)
        page.screenshot(path=str(SHOT_DIR / "04_trace.png"), full_page=True)

        # Ask a follow-up about cluster identity.
        composer.fill("what cell type is the largest cluster?")
        composer.press("Enter")
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=300000,
        )
        page.screenshot(path=str(SHOT_DIR / "05_cluster_id.png"), full_page=True)

        browser.close()

    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}  ({shot.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
