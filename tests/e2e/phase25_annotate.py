"""
Phase 25 e2e (fake mode): spatial-reference plumbing.

Focus a figure, enter Mark mode, drag a circle, Attach → the annotation
chip appears in the chat; sending a message clears it. (The vision model
call is exercised separately in the Haiku acceptance; fake mode just
verifies the UI + payload plumbing.)
"""
from __future__ import annotations
import json, os, shutil, signal, socket, subprocess, sys, tempfile, time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHOT_DIR = ROOT / "tests/e2e/screenshots/phase25"
FIXTURE = ROOT / "tests/fixtures/phase1_focus.jsonl"
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
    work = Path(tempfile.mkdtemp(prefix="aba_p25_"))
    (work / "artifacts").mkdir(); (work / "data").mkdir()
    shutil.copy(ROOT / "backend/data/cells.csv", work / "data/cells.csv")
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
    b = "ababoundary"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"cells.csv\"\r\n"
            f"Content-Type: text/csv\r\n\r\n").encode() + src.read_bytes() + f"\r\n--{b}--\r\n".encode()
    with urllib.request.urlopen(urllib.request.Request(
        f"http://127.0.0.1:{BACKEND_PORT}/api/upload", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"})) as r:
        ds = json.loads(r.read())

    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        page = br.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto(f"http://127.0.0.1:{fport}/", wait_until="networkidle")
        # Workspace
        page.locator('button[title="Workspace"]').click()
        page.locator(f'[data-entity-id="{ds["id"]}"]').click()
        page.wait_for_selector(".focus__preview-table", timeout=5000)
        comp = page.locator(".composer__input")
        comp.fill("plot mt_fraction"); comp.press("Enter")
        page.wait_for_selector('[data-entity-type="figure"]', timeout=15000)
        page.wait_for_function("() => !document.querySelector('.composer__input').disabled", timeout=10000)

        # Focus the figure → AnnotatedFigure renders with a Mark button.
        page.locator('[data-entity-type="figure"]').first.click()
        page.wait_for_selector(".annot__tb-btn", timeout=3000)
        page.locator(".annot__tb-btn").click()  # enter marking
        page.wait_for_selector(".annot__wrap--marking", timeout=2000)

        # Drag a circle across the figure.
        wrap = page.locator(".annot__wrap")
        box = wrap.bounding_box()
        page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.4)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] * 0.8, box["y"] + box["height"] * 0.7, steps=8)
        page.mouse.up()
        page.wait_for_selector(".annot__svg", timeout=2000)
        page.screenshot(path=str(SHOT_DIR / "01_marked.png"), full_page=True)

        # Finishing the stroke auto-attaches — no separate button.
        page.wait_for_selector(".annot-attached", timeout=3000)
        page.screenshot(path=str(SHOT_DIR / "02_attached.png"), full_page=True)
        print("✓ region auto-attached on draw (no attach button)")

        # Send a question → the mark stays attached (sticky) for follow-ups.
        comp.fill("what's in this region?")
        comp.press("Enter")
        page.wait_for_function(
            "() => !document.querySelector('.composer__input').disabled", timeout=15000)
        assert page.locator(".annot-attached").count() == 1, "annotation should stay attached"
        print("✓ annotation sent and stays attached for follow-ups (sticky)")
        # Clear it explicitly via the chip ×.
        page.locator(".annot-attached button").click()
        page.wait_for_selector(".annot-attached", state="detached", timeout=3000)
        print("✓ chip × clears the annotation")
        br.close()
    print("\nscreenshots:")
    for s in sorted(SHOT_DIR.glob("*.png")): print(f"  {s.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try: sys.exit(main())
    except KeyboardInterrupt: sys.exit(130)
