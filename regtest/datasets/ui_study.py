"""Browser-driven UI/UX evaluation study (regtest/datasets/README.md — the
"next round"): the SAME live-agent scenario style, but driven through the REAL
frontend in a REAL browser, evaluating what a scientist actually sees.

Reuses study.py's bootstrap (throwaway ABA_HOME, oauth bridge, real weft) but
serves the app over REAL HTTP (uvicorn thread) and drives it with Playwright:
type into the Composer, watch the Run card progress, click bring-back, read
badges — capturing a SCREENSHOT at every checkpoint for heuristic review
against the §8 card grammar (misc/more_weft_ui.md). Checks stay coarse
(round-trip truths); the screenshots are the real deliverable — reviewed by a
person/agent, findings fed to the more_weft_ui.md backlog.

Run:  python regtest/datasets/ui_study.py [--only name,name]
Requires: `pip install playwright && playwright install chromium` in the env.
Screenshots land in $ABA_UI_SHOTS (default under the throwaway tmp; printed).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study  # noqa: E402 — throwaway home, oauth bridge, portal, init_db

from study import RESULTS, RUN_OUT  # noqa: E402

SHOTS = Path(os.environ.get("ABA_UI_SHOTS") or (study._tmp / "ui_shots"))
SHOTS.mkdir(parents=True, exist_ok=True)


# ── real HTTP server (the SPA + API on one origin, like production) ──────────
def _start_server() -> str:
    import socket
    import uvicorn
    from main import app
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    else:
        sys.exit("[ui] uvicorn did not start")
    return f"http://127.0.0.1:{port}"


# ── checkpoint screenshots ───────────────────────────────────────────────────
_SHOT_N = {"n": 0}


def shot(page, name: str) -> Path:
    """Numbered checkpoint screenshot — the review artifact of this study."""
    _SHOT_N["n"] += 1
    p = SHOTS / f"{_SHOT_N['n']:03d}_{name}.png"
    page.screenshot(path=str(p), full_page=False)
    print(f"    [shot] {p.name}")
    return p


# ── UI driving helpers ───────────────────────────────────────────────────────
def ui_turn(page, text: str, timeout_s: int = 600) -> None:
    """Type into the Composer and send; wait until the turn ENDS (no Stop
    affordance, composer editable again)."""
    box = page.locator("textarea").first
    box.wait_for(state="visible", timeout=15_000)
    box.fill(text)
    box.press("Enter")
    deadline = time.time() + timeout_s
    time.sleep(1.5)
    while time.time() < deadline:
        stops = page.get_by_role("button", name="Stop").count()
        if stops == 0:
            try:
                if box.is_enabled():
                    return
            except Exception:  # noqa: BLE001 — transient re-render
                pass
        time.sleep(2)
    raise TimeoutError(f"turn did not finish within {timeout_s}s")


def wait_text(page, needle: str, timeout_s: int = 120) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if page.get_by_text(needle, exact=False).count() > 0:
                return True
        except Exception:  # noqa: BLE001 — mid-render
            pass
        time.sleep(2)
    return False


# ── per-scenario runner (fresh project; page reloaded into it) ───────────────
UI_SCENARIOS: list = []


def ui_scenario(name):
    def deco(fn):
        UI_SCENARIOS.append((name, fn))
        return fn
    return deco


def _enter_project(page) -> None:
    """The SPA lands on the Projects list — click into the workspace, then
    wait for the composer to mount."""
    btn = page.get_by_role("button", name="Open project")
    try:
        if btn.count():
            btn.first.click()
    except Exception:  # noqa: BLE001 — already inside
        pass
    page.locator("textarea").first.wait_for(state="visible", timeout=20_000)


def run_ui_scenario(page, api, name, fn):
    pid = api.post("/api/projects", json={"name": f"ui-{name}"}).json()["id"]
    api.post(f"/api/projects/{pid}/open")
    tid = api.post("/api/threads",
                   json={"project_id": pid, "title": name}).json()["id"]
    page.goto(page.url.split("#")[0], wait_until="domcontentloaded")  # fresh mount
    _enter_project(page)
    t0 = time.time()
    try:
        checks = fn(page, api, pid, tid)
        ok = all(v for _, v in checks)
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        shot(page, f"{name}_EXCEPTION")
        checks, ok = [("exception", False)], False
    dt = time.time() - t0
    (RUN_OUT / f"{name}.json").write_text(json.dumps(
        {"name": name, "seconds": round(dt, 1),
         "checks": [[c, bool(v)] for c, v in checks]}, indent=1, default=str))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} ({dt:.0f}s)")
    for c, v in checks:
        print(f"    {'✓' if v else '✗'} {c}")
    RESULTS.append((name, ok))


# ── scenarios ────────────────────────────────────────────────────────────────
@ui_scenario("ui_smoke_first_turn")
def ui_smoke_first_turn(page, api, pid, tid):
    """Smoke: the app loads, a chat turn round-trips through the REAL SPA,
    and the reply renders. Screenshots: cold app, reply."""
    shot(page, "app_loaded")
    ui_turn(page, "Compute 17*23 and just tell me the number.")
    ok = wait_text(page, "391", timeout_s=60)
    shot(page, "first_reply")
    return [("app served + turn round-tripped in the browser", ok)]


def main() -> None:
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    base = _start_server()
    print(f"[ui] serving at {base}\n[ui] shots: {SHOTS}")

    import httpx
    from playwright.sync_api import sync_playwright
    with httpx.Client(base_url=base, timeout=60) as api, sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(base, wait_until="domcontentloaded")
        for name, fn in UI_SCENARIOS:
            if only and name not in only:
                continue
            run_ui_scenario(page, api, name, fn)
        browser.close()

    print("\nUI STUDY:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
