"""
Phase 6 mini-acceptance: pbmc3k-style flow without downloading 8MB.

Generates a synthetic 10x v2 layout (tiny: 200 cells × 500 genes), tars it,
uploads via the URL endpoint pointing at a local HTTP server. Then drives
the UI:

  1. Upload the archive
  2. Click the dataset → focus canvas shows file metadata
  3. Ask Guide to inspect and run QC + UMAP (live Haiku in this script)

Costs a few cents of Haiku tokens. Skips silently if no API key.
"""
from __future__ import annotations

import gzip
import http.server
import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase6"
BACKEND_PORT = 8000
NODE_BIN = "/opt/nvm/versions/node/v24.14.1/bin"


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


def make_mock_10x_tarball(dest: Path, n_cells: int = 200, n_genes: int = 500) -> Path:
    """Create a synthetic 10x v2 archive (matrix.mtx + barcodes.tsv + genes.tsv)."""
    import numpy as np
    from scipy.io import mmwrite
    from scipy.sparse import csr_matrix

    work = Path(tempfile.mkdtemp(prefix="aba_mock10x_"))
    layout = work / "mock_pbmc"
    layout.mkdir()

    rng = np.random.default_rng(0)
    X = rng.poisson(2.0, size=(n_cells, n_genes)).astype(int)
    # Add a few MT genes (gene names starting with 'MT-') to exercise QC.
    mt_idx = list(range(min(10, n_genes)))
    X[:, mt_idx] = (rng.poisson(8.0, size=(n_cells, len(mt_idx)))).astype(int)
    # 10x stores genes × cells, sparse.
    X_T = csr_matrix(X.T)
    mmwrite(str(layout / "matrix.mtx"), X_T)

    with (layout / "barcodes.tsv").open("w") as f:
        for i in range(n_cells):
            f.write(f"CELL_{i:04d}-1\n")
    with (layout / "genes.tsv").open("w") as f:
        for j in range(n_genes):
            name = f"MT-GENE{j}" if j in mt_idx else f"GENE{j:04d}"
            f.write(f"ENSG{j:08d}\t{name}\n")

    with tarfile.open(dest, "w:gz") as tf:
        tf.add(layout, arcname="mock_pbmc")
    return dest


def serve_directory(directory: Path):
    """Start a tiny static HTTP server on a free port, return (url-prefix, server)."""
    port = free_port()

    class H(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a, **k): pass
        def __init__(self, *a, **k): super().__init__(*a, directory=str(directory), **k)

    srv = socketserver.TCPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}", srv


def main() -> int:
    key = _env_key()
    if not key or key.startswith("sk-ant-..."):
        print("ANTHROPIC_API_KEY not set; skipping Phase 6 live mini-acceptance.")
        return 0

    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    for p in SHOT_DIR.glob("*.png"): p.unlink()
    if not port_free(BACKEND_PORT):
        print(f"port {BACKEND_PORT} in use", file=sys.stderr); return 2

    work = Path(tempfile.mkdtemp(prefix="aba_phase6_"))
    artifacts = work / "artifacts"; artifacts.mkdir()
    data = work / "data"; data.mkdir()

    # Build the mock archive and serve it.
    serve_root = work / "serve"; serve_root.mkdir()
    archive = serve_root / "mock_pbmc.tar.gz"
    make_mock_10x_tarball(archive)
    print(f"✓ generated mock 10x archive ({archive.stat().st_size} B)")
    serve_url, srv = serve_directory(serve_root)

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
        print(f"backend :{BACKEND_PORT}  frontend :{frontend_port}  data-server {serve_url}")
        rc = drive(frontend_port, serve_url)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(Path(work, "backend.log").read_text()[-3000:], file=sys.stderr)
    finally:
        print(f"logs in {work}")
        srv.shutdown()
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


def drive(frontend_port: int, serve_url: str) -> int:
    from playwright.sync_api import sync_playwright

    # Upload via /api/upload-url
    body = json.dumps({"url": f"{serve_url}/mock_pbmc.tar.gz"}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/upload-url",
        data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req) as r:
        dataset = json.loads(r.read())
    print(f"✓ uploaded mock archive as dataset {dataset['id']}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{frontend_port}/", wait_until="networkidle")

        # Focus the archive entity.
        page.locator(f'[data-entity-id="{dataset["id"]}"]').click()
        page.wait_for_function(
            "() => document.querySelector('.focus-chip').textContent.includes('dataset')",
            timeout=3000,
        )
        page.screenshot(path=str(SHOT_DIR / "01_archive_focused.png"), full_page=True)

        # Ask Guide to inspect + analyze. The system prompt now mentions
        # inspect_upload, scanpy, and the compact pipeline.
        composer = page.locator(".composer__input")
        composer.fill(
            "this looks like a 10x archive — inspect it, then run a small "
            "scanpy pipeline (QC, normalize, PCA, UMAP, leiden)"
        )
        composer.press("Enter")

        # Wait for completion — generous, since this is a real pipeline run.
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled",
            timeout=180000,
        )
        page.screenshot(path=str(SHOT_DIR / "02_after_pipeline.png"), full_page=True)

        # Toggle trace to see the inner loop the model produced.
        page.locator(".trace-toggle").click()
        page.wait_for_selector(".trace-card", timeout=5000)
        page.screenshot(path=str(SHOT_DIR / "03_trace.png"), full_page=True)

        # Click whichever figure landed last in the tree.
        figures = page.locator('[data-entity-type="figure"]')
        n_figs = figures.count()
        print(f"✓ pipeline registered {n_figs} figure(s)")
        if n_figs > 0:
            figures.last.click()
            page.wait_for_selector(".focus__figure", timeout=5000)
            page.screenshot(path=str(SHOT_DIR / "04_final_figure.png"), full_page=True)

        browser.close()

    print("\nscreenshots:")
    for shot in sorted(SHOT_DIR.glob("*.png")):
        print(f"  {shot.relative_to(ROOT)}  ({shot.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
