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


def _open_data_panel(page) -> None:
    """Open the project tree at the Data section — the rail button TOGGLES,
    so only click while the panel is closed (marker: the section header)."""
    for _ in range(3):
        try:
            if (page.get_by_text("ACTIVE DATASETS", exact=False).count()
                    or page.locator(".ledger").count()):
                return
            page.get_by_text("Data", exact=True).first.click()
        except Exception:  # noqa: BLE001 — mid-render
            pass
        time.sleep(2)


def _enter_project(page) -> None:
    """The SPA lands on the Projects list — click into the workspace and wait
    for the composer. RETRIES the click: right after a reload the button can
    render before React attaches its handler, so a single early click focuses
    but never navigates."""
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if page.locator("textarea").first.is_visible():
                return
        except Exception:  # noqa: BLE001 — mid-render
            pass
        try:
            btn = page.get_by_role("button", name="Open project")
            if btn.count():
                btn.first.click()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    page.locator("textarea").first.wait_for(state="visible", timeout=5_000)


def run_ui_scenario(page, api, name, fn):
    pid = api.post("/api/projects", json={"name": f"ui-{name}"}).json()["id"]
    api.post(f"/api/projects/{pid}/open")
    tid = api.post("/api/threads",
                   json={"project_id": pid, "title": name}).json()["id"]
    t0 = time.time()
    try:
        page.goto(BASE["url"], wait_until="domcontentloaded")   # fresh mount
        _enter_project(page)
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


BASE = {"url": ""}          # set in main
HPC = {"ok": False}         # docker slurm fixture registered (optional)


def _new_entities(kind: str, pre: set, frag: str | None = None) -> list:
    """Snapshot-scoped entity lookup (the study shares ONE DB across
    scenarios — global 'some X exists' checks can never fail)."""
    from core.graph.entities import find_entities
    out = [e for e in find_entities(type=kind, not_deleted=True)
           if e["id"] not in pre]
    if frag:
        out = [e for e in out if frag.lower() in (e.get("title") or "").lower()]
    return out


def _snap(kind: str) -> set:
    from core.graph.entities import find_entities
    return {e["id"] for e in find_entities(type=kind, not_deleted=True)}


# ── scenarios (order matters: the ledger QUIET check must precede anything
#    that wakes the strip — remote homes, drifted sources) ────────────────────
@ui_scenario("ui_smoke_first_turn")
def ui_smoke_first_turn(page, api, pid, tid):
    """Smoke: the app loads, a chat turn round-trips through the REAL SPA,
    and the reply renders. Screenshots: cold app, reply."""
    shot(page, "app_loaded")
    ui_turn(page, "Compute 17*23 and just tell me the number.")
    ok = wait_text(page, "391", timeout_s=60)
    shot(page, "first_reply")
    return [("app served + turn round-tripped in the browser", ok)]


@ui_scenario("ui_ledger_quiet_then_remote")
def ui_ledger_quiet_then_remote(page, api, pid, tid):
    """LedgerStrip contract in the project TREE panel (left rail → Data):
    an all-local, all-safe project renders ZERO ledger chrome (absence is
    the default); once a remote-homed dataset exists the strip appears."""
    _open_data_panel(page)
    quiet = page.locator(".ledger").count() == 0
    shot(page, "tree_panel_quiet")
    if not HPC["ok"]:
        return [("quiet when local-only", quiet),
                ("hpc fixture available", False)]
    from multinode import hssh, R_DATA
    hssh(f"mkdir -p {R_DATA} && (echo a,b; seq 1 100 | "
         f"awk '{{print $1\",\"($1%7)}}') > {R_DATA}/led_probe.csv")
    ui_turn(page, f"The file {R_DATA}/led_probe.csv lives on machine 'hpc'. "
                  f"Register it as a dataset called 'Led probe' by REFERENCE "
                  f"— do not copy it here.")
    appeared = False
    deadline = time.time() + 60
    while time.time() < deadline and not appeared:
        _open_data_panel(page)
        appeared = page.locator(".ledger").count() > 0
        time.sleep(2)
    shot(page, "tree_panel_remote_ledger")
    return [("quiet when local-only", quiet),
            ("strip appears once a remote home exists", appeared)]


@ui_scenario("ui_run_card_lifecycle")
def ui_run_card_lifecycle(page, api, pid, tid):
    """Run card (§8): after a completed local run, the card shows the run,
    its output files, and their keep state."""
    pre = _snap("analysis")
    ui_turn(page, "Open an analysis run titled 'Curve study'. Compute "
                  "y = 3*i + 1 for i in 1..40, save it as curve.csv, and make "
                  "a simple line plot of it. Keep both outputs in the run.")
    runs = _new_entities("analysis", pre, "curve study")
    if not runs:
        shot(page, "run_missing")
        return [("run created", False)]
    page.goto(f"{BASE['url']}/p/{pid}/e/{runs[0]['id']}",
              wait_until="domcontentloaded")
    time.sleep(3)
    got_title = wait_text(page, "Curve study", 30)
    got_file = wait_text(page, "curve.csv", 30)
    shot(page, "run_card")
    # §8c: while the run is open/settling the state reads "keeping…"; after
    # settlement "kept ✓" — both are keep-state truth
    import re as _re
    kept = page.get_by_text(_re.compile(r"keep|kept")).count() > 0
    return [("run created", True), ("card shows the run", got_title),
            ("outputs listed on the card", got_file),
            ("keep state visible (keeping…/kept)", kept)]


@ui_scenario("ui_pin_flow")
def ui_pin_flow(page, api, pid, tid):
    """Chat figure → the pin glyph → a Result entity; the glyph reflects the
    pinned state (the optimistic-state divergence bug lived here)."""
    pre = _snap("result")
    ui_turn(page, "Make a quick scatter plot of x=i, y=i*i for i in 1..30 "
                  "and show it to me here. No need to open a run.")
    pin = page.locator('[title="Pin this figure"]').first
    try:
        pin.wait_for(state="visible", timeout=20_000)
    except Exception:  # noqa: BLE001
        shot(page, "pin_no_affordance")
        return [("figure with pin affordance rendered", False)]
    shot(page, "figure_prepin")
    pin.click()
    time.sleep(5)
    shot(page, "figure_pinned")
    new_results = _new_entities("result", pre)
    pinned_vis = page.locator('[title^="Pinned"]').count() > 0
    return [("THIS pin created a Result", bool(new_results)),
            ("glyph reflects the pinned state", pinned_vis)]


@ui_scenario("ui_drift_banner")
def ui_drift_banner(page, api, pid, tid):
    """§5 drift: a registered-by-reference source changes on disk → the
    dataset card carries the banner (recheck / relink / new-version) and the
    relink input opens."""
    src = study.www / "drift_table.csv"
    src.write_text("k,v\n" + "\n".join(f"{i},{i * 2}" for i in range(50)) + "\n")
    pre = _snap("dataset")
    ui_turn(page, f"Register the local file at {src} as a dataset called "
                  f"'Drift probe' by REFERENCE — leave it in place, no copy.")
    ds = _new_entities("dataset", pre, "drift probe")
    if not ds:
        return [("dataset registered", False)]
    src.write_text("k,v\n1,999\n")                     # the source mutates
    api.post(f"/api/datasets/{ds[0]['id']}/recheck")
    page.goto(f"{BASE['url']}/p/{pid}/e/{ds[0]['id']}",
              wait_until="domcontentloaded")
    time.sleep(2)
    banner = wait_text(page, "has changed since registration", 20)
    shot(page, "drift_banner")
    relink_open = False
    try:
        page.get_by_text("It moved — relink").first.click()
        time.sleep(1)
        relink_open = page.get_by_placeholder(
            "new path of the same data…").count() > 0
        shot(page, "drift_relink_input")
    except Exception:  # noqa: BLE001
        pass
    return [("dataset registered", True), ("drift banner shown", banner),
            ("relink input opens", relink_open)]


@ui_scenario("ui_remote_run_badges")
def ui_remote_run_badges(page, api, pid, tid):
    """Remote truth on the Run card: a large output kept on hpc reads
    'on hpc'; the Bring back affordance lands a local copy ('copy here')."""
    if not HPC["ok"]:
        return [("hpc fixture available", False)]
    pre = _snap("analysis")
    ui_turn(page,
            "Open an analysis run titled 'Remote bulk'. Run a BACKGROUND job "
            "on machine 'hpc' that writes a LARGE ~60 MB file called big.bin "
            "in the run's working directory (60*1024*1024 bytes). It's big — "
            "keep it SAFE on hpc as one of the RUN's own kept outputs "
            "(keep_outputs), do NOT register it as a separate dataset and do "
            "not copy it here.", timeout_s=420)
    from multinode import wait_jobs_settled
    wait_jobs_settled(api, pid)
    runs = _new_entities("analysis", pre, "remote bulk")
    if not runs:
        return [("run created", False)]
    rid = runs[0]["id"]
    # GROUND TRUTH first: wait until the durable view actually records the
    # kept remote file (the keep lands in a continuation AFTER the turn) —
    # only then is there anything for the card to render.
    kept_remote = False
    deadline = time.time() + 240
    while time.time() < deadline and not kept_remote:
        dv = api.get(f"/api/runs/{rid}/durable").json()
        kept_remote = any(f.get("state") == "retained" and f.get("site") == "hpc"
                          for f in dv.get("files", []))
        time.sleep(6)
    card = page.locator(".runview")           # scope to the CARD — chat text
    page.goto(f"{BASE['url']}/p/{pid}/e/{rid}", wait_until="domcontentloaded")
    time.sleep(5)
    shot(page, "run_card_remote")
    verdict_remote = card.get_by_text("ran on hpc", exact=False).count() > 0
    badge_remote = card.get_by_text("kept ✓ · on hpc", exact=False).count() > 0
    brought = False
    bb = card.get_by_text("bring the rest back", exact=False)
    if bb.count():
        bb.first.click()
        deadline = time.time() + 150          # whereabouts line clears when
        while time.time() < deadline:         # every file has local bytes
            time.sleep(6)
            if card.get_by_text("bring the rest back", exact=False).count() == 0:
                brought = True
                break
        shot(page, "run_card_after_bringback")
    return [("run created", True),
            ("durable view records the kept remote file", kept_remote),
            ("verdict says ran on hpc (placement fix)", verdict_remote),
            ("CARD badge reads kept ✓ · on hpc", badge_remote),
            ("bring-back lands local bytes (whereabouts clears)", brought)]


def _register_hpc() -> None:
    """Best-effort: the dockerized slurm fixture (multinode's), for the
    remote-truth UI scenarios. Skipped cleanly when absent."""
    try:
        from multinode import _cluster_conn
        from core.compute import adapter as ad
        conn = _cluster_conn()
        if not conn:
            return
        st = ad.configure()
        if not st.get("ok"):
            return
        ad.get_compute().sync_call("register_site", "hpc", "slurm", {
            "root": "/home/physicist/.weft", "host": "127.0.0.1",
            "port": conn["port"], "user": "physicist", "durable": True,
            "ssh_opts": ["-i", f"{conn['keydir']}/id_ed25519",
                         "-o", "StrictHostKeyChecking=no",
                         "-o", "UserKnownHostsFile=/dev/null",
                         "-o", "IdentitiesOnly=yes"]})
        HPC["ok"] = True
        print("[ui] hpc fixture registered")
    except Exception as e:  # noqa: BLE001
        print(f"[ui] no hpc fixture ({e}) — remote scenarios will skip")


def main() -> None:
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    base = _start_server()
    BASE["url"] = base
    _register_hpc()
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
