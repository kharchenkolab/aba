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


def _open_data_panel(page) -> bool:
    """Open the project tree at the Data section — the rail button TOGGLES,
    so only click while the panel is closed (marker: the section header).
    Returns whether the panel is verifiably OPEN: absence-based assertions
    (e.g. "no ledger chrome") are vacuous against a never-opened panel."""
    for _ in range(3):
        try:
            if (page.get_by_text("ACTIVE DATASETS", exact=False).count()
                    or page.locator(".ledger").count()):
                return True
            page.get_by_text("Data", exact=True).first.click()
        except Exception:  # noqa: BLE001 — mid-render
            pass
        time.sleep(2)
    return bool(page.get_by_text("ACTIVE DATASETS", exact=False).count()
                or page.locator(".ledger").count())


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
        checks = list(fn(page, api, pid, tid))
        # global invariant: no scenario may leave a job row contradicting the
        # substrate (false failures / double-finalize residue) — same sweep
        # the API-level runners append
        from study import verify_jobs_truth
        violations = verify_jobs_truth()
        if violations:
            checks += [(f"truth-sweep: {v}", False) for v in violations]
        else:
            checks.append(("jobs-vs-substrate truth sweep clean", True))
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
    opened = _open_data_panel(page)
    # absence claim requires the panel to be verifiably open — otherwise a
    # never-mounted panel "has no ledger" vacuously (recheck finding)
    quiet = opened and page.locator(".ledger").count() == 0
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
    # settlement "kept ✓" — both are keep-state truth. Match the exact badge
    # vocabulary: a bare `keep|kept` regex was satisfiable by "not kept", any
    # Keep button, etc. (recheck finding — an assertion failure states can pass)
    import re as _re
    kept = page.get_by_text(_re.compile(r"keeping…|keeping\.\.\.|kept ✓")).count() > 0
    return [("run created", True), ("card shows the run", got_title),
            ("outputs listed on the card", got_file),
            ("keep state visible (keeping…/kept ✓)", kept)]


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
        dv = api.get(f"/api/runs/{rid}/durable?flat=1").json()   # default is the TREE model
        kept_remote = any(f.get("state") == "retained" and f.get("site") == "hpc"
                          for f in dv.get("files", []))
        time.sleep(6)
    card = page.locator(".runview")           # scope to the CARD — chat text
    page.goto(f"{BASE['url']}/p/{pid}/e/{rid}", wait_until="domcontentloaded")
    time.sleep(5)
    shot(page, "run_card_remote")
    verdict_remote = card.get_by_text("ran on hpc", exact=False).count() > 0
    badge_remote = card.get_by_text("kept ✓ · on hpc", exact=False).count() > 0
    if not kept_remote:                       # the keep can land AFTER the poll
        dv = api.get(f"/api/runs/{rid}/durable?flat=1").json()   # gave up — resample so
        kept_remote = any(f.get("state") == "retained" and f.get("site") == "hpc"
                          for f in dv.get("files", []))   # ordering can't fail us
    # INDEPENDENT ground truth (recheck finding: the durable endpoint is the
    # same server-side source the card renders from): stat the bytes on the
    # NODE itself — a >50MB big.bin under the fixture's weft tree.
    node_truth = False
    try:
        from multinode import hssh
        out = hssh("find /home/physicist/.weft -name big.bin -size +50M "
                   "2>/dev/null | head -1")
        node_truth = bool((out.stdout or "").strip())
    except Exception as e:  # noqa: BLE001
        print(f"    [probe] node stat unavailable: {e}")
    brought = False
    brought_bytes = False
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
        # POSITIVE evidence, not just disappearance (recheck finding: the line
        # also vanishes if the card unmounts/errors): the brought-back BYTES
        # exist on this machine's disk. (The flat row's `url` is only minted
        # for small artifact-store copies — a 60 MB file never gets one, so
        # disk truth is the honest probe; first live round tripped on that.)
        import glob as _glob
        import os as _os
        roots = [str(getattr(study, "_tmp", "") or "")]
        brought_bytes = any(
            _os.path.getsize(p) >= 50 * 1024 * 1024
            for r in roots if r
            for p in _glob.glob(r + "/**/big.bin", recursive=True))
    return [("run created", True),
            ("durable view records the kept remote file", kept_remote),
            ("NODE-level truth: kept bytes exist on hpc (ssh stat)", node_truth),
            ("verdict says ran on hpc (placement fix)", verdict_remote),
            ("CARD badge reads kept ✓ · on hpc", badge_remote),
            ("bring-back lands local bytes (whereabouts clears)", brought),
            ("bring-back POSITIVE: flat row serves local bytes", brought_bytes)]


@ui_scenario("ui_cancel_midrun")
def ui_cancel_midrun(page, api, pid, tid):
    """Mid-run interruption FROM THE UI (release_test_plan: 'Mid-run cancel
    from the UI'): send a slow direct step, hit Stop while it runs. The turn
    must end promptly (not ride out the sleep), the step's completion marker
    must NEVER be claimed, and the thread must not be wedged — a follow-up
    turn works. Marker is COMPUTED (50+50) so it cannot appear in the user
    bubble and rig the absence check."""
    box = page.locator("textarea").first
    box.wait_for(state="visible", timeout=15_000)
    box.fill("Run a quick local python step that sleeps 90 seconds and then "
             "prints SLOWMARK- immediately followed by the sum of 50+50. "
             "Run it directly, not in the background.")
    box.press("Enter")
    stop = page.locator(".composer__stop")
    try:
        stop.wait_for(state="visible", timeout=30_000)
    except Exception:  # noqa: BLE001
        return [("Stop affordance appears while a turn runs", False)]
    time.sleep(12)                       # let the tool actually start
    shot(page, "cancel_midrun_before_stop")
    stop.click()
    ended = False
    t0 = time.time()
    while time.time() - t0 < 60:         # 90s sleep → must end well before
        if page.locator(".composer__stop").count() == 0:
            ended = True
            break
        time.sleep(2)
    shot(page, "cancel_midrun_after_stop")
    time.sleep(8)                        # let any dishonest late text land
    fabricated = page.get_by_text("SLOWMARK-100", exact=False).count() > 0
    # F4 guard: the cancelled turn must RENDER (possibly empty/partial) —
    # never the ErrorBoundary's failure banner
    render_ok = page.get_by_text("couldn’t be displayed").count() == 0 and \
        page.get_by_text("couldn't be displayed").count() == 0
    # durable stop marker (F4 follow-up): the stop is RECORDED in the
    # thread, not just an absence
    marker_ok = page.get_by_text("stopped by user", exact=False).count() > 0
    followup_ok = True
    try:
        ui_turn(page, "Just tell me: what is 6*7? Answer with the number.",
                timeout_s=120)
        followup_ok = wait_text(page, "42", timeout_s=30)
    except Exception:  # noqa: BLE001
        followup_ok = False
    shot(page, "cancel_midrun_followup")
    return [("Stop ends the turn promptly (not riding out the sleep)", ended),
            ("no fabricated completion after cancel", not fabricated),
            ("cancelled turn renders (no ErrorBoundary banner — F4)",
             render_ok),
            ("durable '(stopped by user)' marker recorded", marker_ok),
            ("thread usable after cancel (follow-up turn works)", followup_ok)]


@ui_scenario("ui_reload_reconnect")
def ui_reload_reconnect(page, api, pid, tid):
    """Resume-after-reload (release_test_plan: 'Resume after reload'): the
    user reloads the tab mid-turn. The reloaded UI must reconnect to the
    still-running durable turn and render its completion — work survives the
    reload. Marker computed (40+2) so the needle can only come from the
    step's real output."""
    box = page.locator("textarea").first
    box.wait_for(state="visible", timeout=15_000)
    box.fill("Run a quick local python step that sleeps 35 seconds and then "
             "prints RELOADMARK- immediately followed by the sum of 40+2. "
             "Run it directly, not in the background, then tell me the "
             "printed value.")
    box.press("Enter")
    stop = page.locator(".composer__stop")
    try:
        stop.wait_for(state="visible", timeout=30_000)
    except Exception:  # noqa: BLE001
        return [("turn started (Stop visible)", False)]
    time.sleep(8)
    shot(page, "reload_midrun")
    page.reload(wait_until="domcontentloaded")
    shot(page, "reload_just_after")
    reconnected = False
    try:
        page.locator(".composer__stop").wait_for(state="visible",
                                                 timeout=20_000)
        reconnected = True
    except Exception:  # noqa: BLE001 — turn may have finished already
        pass
    ok = wait_text(page, "RELOADMARK-42", timeout_s=180)
    shot(page, "reload_result")
    return [("UI reconnects to the in-flight turn after reload", reconnected),
            ("the step's true output renders after the reload", ok)]


@ui_scenario("ui_failed_step_render")
def ui_failed_step_render(page, api, pid, tid):
    """ERROR-STATE rendering (release_test_plan: 'UI error/empty states'):
    a step that RAISES must surface as an honest failure in the thread —
    an error-marked tool line and an agent acknowledgment — never a crash
    banner, never a claimed success. Marker computed (30+40) so the needle
    can't come from the user bubble."""
    box = page.locator("textarea").first
    box.wait_for(state="visible", timeout=15_000)
    box.fill("Run a quick local python step that first computes 30+40 and "
             "then raises RuntimeError('deliberate-test-error-' followed by "
             "that number). Just report what happened — do not retry or "
             "work around it.")
    box.press("Enter")
    ok_end = True
    try:
        deadline = time.time() + 240
        while time.time() < deadline:
            if page.locator(".composer__stop").count() == 0 and \
                    box.is_enabled():
                break
            time.sleep(2)
        else:
            ok_end = False
    except Exception:  # noqa: BLE001
        ok_end = False
    time.sleep(3)
    shot(page, "failed_step_render")
    body_txt = ""
    try:
        body_txt = page.locator(".chat-pane, main, body").first.inner_text()
    except Exception:  # noqa: BLE001
        pass
    crash = ("couldn’t be displayed" in body_txt
             or "couldn't be displayed" in body_txt)
    acked = any(w in body_txt.lower() for w in
                ("error", "failed", "raise", "deliberate-test-error"))
    claimed_ok = "deliberate-test-error-70" in body_txt and \
        not any(w in body_txt.lower() for w in ("error", "fail", "raise"))
    return [("turn ended", ok_end),
            ("no crash banner (ErrorBoundary) anywhere", not crash),
            ("failure acknowledged in the thread", acked),
            ("no silent success claim", not claimed_ok)]


@ui_scenario("ui_settings_compute_connect")
def ui_settings_compute_connect(page, api, pid, tid):
    """Settings→Compute ONBOARDING journey, end-to-end in the browser against
    a REAL detached machine (mendel): open Settings, add the machine by the
    same thing you'd type after `ssh`, ride preflight→probe→proposal, pin the
    working space to a disposable path, confirm, verify the card + its edit
    affordances, test the connection, then disconnect. This is the whole
    first-contact path a new user walks before any analysis exists
    (release_test_plan: 'Compute-sites — connect/probe/propose')."""
    from multinode import mssh
    if mssh("echo ok").stdout.strip() != "ok":
        return [("mendel available (scenario skipped otherwise)", False)]
    ui_root = "/home/pkharchenko/aba-uistudy-weft"
    checks = []
    try:
        page.locator(".rail__user").click()
        page.locator(".settings__tab", has_text="Compute").click()
        page.wait_for_timeout(800)
        shot(page, "settings_compute_initial")
        body = page.locator(".settings__body")
        checks.append(("Compute tab renders with an add affordance",
                       body.get_by_text("+ Add remote compute").count() > 0))
        body.get_by_text("+ Add remote compute").click()
        page.locator('input[name="cmp-ssh-dest"]').fill("mendel")
        shot(page, "connect_entry")
        page.get_by_role("button", name="Continue").click()
        # preflight → probe → proposal; bootstrap on a real machine takes time
        add_btn = page.get_by_role("button", name="Add", exact=True)
        prop_ok = True
        try:
            add_btn.wait_for(state="visible", timeout=240_000)
        except Exception:  # noqa: BLE001
            prop_ok = False
        shot(page, "connect_proposal")
        checks.append(("probe reached a proposal (detached machine)", prop_ok))
        if not prop_ok:
            return checks
        text = body.inner_text()
        # the proposed name lives in an input VALUE — inner_text misses it
        pname = page.locator('input[name="cmp-site-name"]').input_value()
        checks.append(("proposal names the machine", "mendel" in pname.lower()))
        # detached wording must be honest — the flow SUPPORTS detached now
        checks.append(("no stale 'not yet supported' copy",
                       "not yet supported" not in text))
        # working space → a disposable path (never the machine's default root)
        ws = page.locator(".cmp-form select").first
        try:
            ws.select_option("__custom__")
            sec = ws.locator("xpath=..")
            sec.locator("input:not([type='checkbox'])").first.fill(ui_root)
            root_ok = True
        except Exception:  # noqa: BLE001
            root_ok = False
        checks.append(("working space can be pointed at a custom path",
                       root_ok))
        shot(page, "connect_proposal_custom_root")
        add_btn.click()
        # the card appears once registration lands (bootstrap narration runs)
        card = page.locator("[role='button'][aria-expanded]",
                            has_text="mendel").first
        card_ok = True
        try:
            card.wait_for(state="visible", timeout=240_000)
        except Exception:  # noqa: BLE001
            card_ok = False
        checks.append(("machine card appears after Add", card_ok))
        shot(page, "compute_card_added")
        if card_ok:
            # F2 guard: once the card exists, the connect pane must hand over
            # to it — no lingering "Adding the machine…" beside a live card
            pane_gone = True
            try:
                body.get_by_text("Adding the machine").wait_for(
                    state="detached", timeout=30_000)
            except Exception:  # noqa: BLE001
                pane_gone = body.get_by_text("Adding the machine").count() == 0
            checks.append(("connect pane closes once the card exists (F2)",
                           pane_gone))
            # bootstrap continues after the card appears; the full edit
            # affordances (working-space change…) render once Ready
            try:
                card.get_by_text("Ready").wait_for(timeout=240_000)
            except Exception:  # noqa: BLE001
                pass
            card.click()
            page.wait_for_timeout(600)
            shot(page, "compute_card_expanded")
            expanded = body.inner_text()
            for aff in ("change…", "Test connection", "Disconnect…"):
                checks.append((f"edit affordance present: {aff}",
                               aff in expanded))
            body.get_by_role("button", name="Test connection").click()
            conn_ok = True
            try:
                body.get_by_text("connection ok").wait_for(timeout=90_000)
            except Exception:  # noqa: BLE001
                conn_ok = False
            checks.append(("Test connection round-trips ok", conn_ok))
            shot(page, "compute_test_connection")
            body.get_by_role("button", name="Disconnect…").click()
            page.wait_for_timeout(1000)
            shot(page, "compute_disconnect_confirm")
            confirm = page.locator(".cmp-confirm")
            checks.append(("disconnect confirm previews consequences",
                           confirm.count() > 0 and
                           "forget this machine" in confirm.inner_text()))
            confirm.get_by_role("button", name="Disconnect",
                                exact=True).click()
            gone = True
            try:
                card.wait_for(state="detached", timeout=60_000)
            except Exception:  # noqa: BLE001
                gone = ("mendel" not in body.inner_text().lower())
            checks.append(("card gone after disconnect", gone))
            shot(page, "compute_after_disconnect")
    finally:
        mssh(f"rm -rf {ui_root}")
    return checks


@ui_scenario("ui_zero_byte_output")
def ui_zero_byte_output(page, api, pid, tid):
    """EMPTY-OUTPUT + KEEP-HONESTY (release_test_plan 'UI error/empty
    states'). Two separate dimensions, first live run conflated them:
    (a) SIZE — a zero-byte file with a HARVESTED extension (.txt) must
    render as a kept 0 B row, not blank/NaN/crash; (b) EXTENSION — a file
    outside the harvest allowlist (.dat) is invisible to the tracked
    inventory, and a keep naming it must DISCLOSE the gap (the tool's
    NOT-COVERED guard), never silently count 1 of 2."""
    pre = _snap("analysis")
    ui_turn(page,
            "Open an analysis run titled 'Empty artifact'. In it run a quick "
            "local python step that writes TWO files in the run's working "
            "directory: empty_marker.txt with NOTHING in it (exactly zero "
            "bytes) and blob_probe.dat containing 200 bytes of anything. "
            "Keep BOTH as the run's outputs (keep_outputs), then close the "
            "run and tell me exactly what ended up protected.",
            timeout_s=420)
    from multinode import wait_jobs_settled
    wait_jobs_settled(api, pid)
    # capture the CHAT surface before navigating away — the browser talks
    # to the project's default thread, not `tid` (the two_tabs lesson)
    chat_txt = ""
    try:
        chat_txt = page.locator("body").inner_text().lower()
    except Exception:  # noqa: BLE001
        pass
    runs = _new_entities("analysis", pre, "empty artifact")
    if not runs:
        return [("run created", False)]
    rid = runs[0]["id"]
    # "saving" is the honest state until the kernel's deferred pin settles
    # at kernel stop (close does NOT stop the shared kernel) — demanding
    # "retained" raced that settle on the first runs. Rows carry `bytes`,
    # not `size` (second first-run scenario bug).
    zero_row = None
    deadline = time.time() + 180
    while time.time() < deadline and zero_row is None:
        dv = api.get(f"/api/runs/{rid}/durable?flat=1").json()
        for f in dv.get("files", []):
            if (f.get("rel") or "").endswith("empty_marker.txt") \
                    and f.get("state") in ("retained", "saving"):
                zero_row = f
        time.sleep(6)
    page.goto(f"{BASE['url']}/p/{pid}/e/{rid}", wait_until="domcontentloaded")
    time.sleep(5)
    shot(page, "zero_byte_run")
    body_txt = ""
    try:
        body_txt = page.locator("body").inner_text()
    except Exception:  # noqa: BLE001
        pass
    # (b): the agent must have DISCLOSED the .dat gap to the user (the
    # keep tool now reports NOT COVERED literals; read from the chat
    # surface captured above)
    disclosed = ("blob_probe.dat" in chat_txt
                 and any(w in chat_txt for w in
                         ("not covered", "not protected", "wasn't kept",
                          "was not kept", "couldn't be kept",
                          "could not be kept", "not in the run's tracked",
                          "not tracked")))
    return [
        ("run created", True),
        ("zero-byte .txt is kept/keeping in the durable view",
         zero_row is not None),
        ("durable bytes is 0 (not null/garbage)",
         (zero_row or {}).get("bytes") == 0),
        ("row renders on the card", "empty_marker.txt" in body_txt),
        ("size renders as 0 B next to the row (not blank/NaN)",
         (lambda i: i >= 0 and "0 B" in body_txt[max(0, i - 200):i + 200])
         (body_txt.find("empty_marker.txt"))),
        ("uncovered .dat keep DISCLOSED to the user (not silent)",
         disclosed),
        ("no crash banner", "couldn't be displayed" not in body_txt
         and "couldn’t be displayed" not in body_txt),
    ]


@ui_scenario("ui_unknown_retention_chip")
def ui_unknown_retention_chip(page, api, pid, tid):
    """OUTAGE honesty (ux lesson L1 — surface parity; ops-realism axis):
    with the substrate UP but the retention index unreachable, kept files
    must read 'unknown — retention unreachable' — never 'discarded — it was
    not kept' (that would be a lie about durably-kept bytes). Injected at
    the real seam: the in-process uvicorn imports the same
    core.compute.retention module object, so patching retained/inventory to
    raise IS the outage, end to end through the API and the browser."""
    if not HPC["ok"]:
        return [("hpc fixture available", False)]
    pre = _snap("analysis")
    ui_turn(page,
            "Open an analysis run titled 'Outage probe'. Run a BACKGROUND "
            "job ON machine 'hpc' that writes a ~60 MB file probe_out.bin "
            "(60*1024*1024 bytes) into the run's working directory; keep it "
            "IN PLACE on hpc as the run's kept output (keep_outputs, no "
            "copy here — it's big). Then close the run.", timeout_s=420)
    from multinode import wait_jobs_settled
    wait_jobs_settled(api, pid)
    runs = _new_entities("analysis", pre, "outage probe")
    if not runs:
        return [("run created", False)]
    rid = runs[0]["id"]
    kept = False
    deadline = time.time() + 240
    while time.time() < deadline and not kept:
        dv = api.get(f"/api/runs/{rid}/durable?flat=1").json()
        kept = any((f.get("rel") or "").endswith("probe_out.bin")
                   and f.get("state") == "retained"
                   for f in dv.get("files", []))
        time.sleep(6)
    from core.compute import retention as _ret
    orig_ret, orig_inv = _ret.retained, _ret.inventory
    orig_stat = _ret.file_stat

    def _boom(*a, **k):  # noqa: ANN001
        raise RuntimeError("injected retention outage (ui_study)")
    # ALL retention truth channels dark (first run patched retained+
    # inventory only; file_stat still answered and the file fell into the
    # live/at-risk branch instead of unknown)
    _ret.retained = _boom
    _ret.inventory = _boom
    _ret.file_stat = _boom
    try:
        dv2 = api.get(f"/api/runs/{rid}/durable?flat=1").json()
        page.goto(f"{BASE['url']}/p/{pid}/e/{rid}",
                  wait_until="domcontentloaded")
        time.sleep(5)
        shot(page, "unknown_retention_outage")
        body_txt = ""
        try:
            body_txt = page.locator("body").inner_text()
        except Exception:  # noqa: BLE001
            pass
    finally:
        _ret.retained = orig_ret
        _ret.inventory = orig_inv
        _ret.file_stat = orig_stat
    row2 = next((f for f in dv2.get("files", [])
                 if (f.get("rel") or "").endswith("probe_out.bin")), None)
    # recovery: the outage lifted → the kept truth must come back
    dv3 = api.get(f"/api/runs/{rid}/durable?flat=1").json()
    kept_again = any((f.get("rel") or "").endswith("probe_out.bin")
                     and f.get("state") == "retained"
                     for f in dv3.get("files", []))
    return [
        ("kept on hpc before the outage", kept),
        ("during outage the row reads unknown (not discarded)",
         (row2 or {}).get("state") == "unknown"),
        ("chip renders the unknown state",
         "unknown — retention unreachable" in body_txt),
        ("no 'discarded' lie about the kept file",
         "it was not kept" not in body_txt),
        ("truth returns after the outage", kept_again),
    ]


@ui_scenario("ui_failed_run_card")
def ui_failed_run_card(page, api, pid, tid):
    """FAILED-RUN card state (release_test_plan 'UI error/empty states'):
    a run whose only step raises must render an HONEST failed state on the
    entity card — an error-marked step, no eternal spinner, no crash
    banner. Marker computed (60+13) so it can't come from the prompt."""
    pre = _snap("analysis")
    ui_turn(page,
            "Open an analysis run titled 'Doomed run'. In it run ONE local "
            "python step that computes 60+13 and then raises "
            "RuntimeError('doomed-' followed by that number). Do NOT retry "
            "or work around it — close the run noting that it failed.",
            timeout_s=420)
    from multinode import wait_jobs_settled
    wait_jobs_settled(api, pid)
    runs = _new_entities("analysis", pre, "doomed")
    if not runs:
        return [("run created", False)]
    rid = runs[0]["id"]
    page.goto(f"{BASE['url']}/p/{pid}/e/{rid}", wait_until="domcontentloaded")
    time.sleep(5)
    shot(page, "failed_run_card")
    card = page.locator(".runview")
    ctxt = ""
    try:
        ctxt = (card.first.inner_text() if card.count()
                else page.locator("body").inner_text())
    except Exception:  # noqa: BLE001
        pass
    lower = ctxt.lower()
    # review F5: POSITIVE ground truth first — the run's own exec records
    # must carry the failed step (substring matches on page chrome can't
    # prove THIS run failed).
    exec_failed = False
    try:
        from core.graph import exec_records as _xr
        exec_failed = any((r.get("status") or "") not in ("ok", "")
                          for r in _xr.list_by_run(rid))
    except Exception:  # noqa: BLE001
        pass
    return [
        ("run created", True),
        ("GROUND TRUTH: a failed exec record on this run", exec_failed),
        ("failure marked on the card",
         "error" in lower or "failed" in lower or "✗" in ctxt),
        ("no crash banner", "couldn't be displayed" not in ctxt
         and "couldn’t be displayed" not in ctxt),
    ]


@ui_scenario("ui_two_tabs")
def ui_two_tabs(page, api, pid, tid):
    """SAME-USER CONCURRENCY (release_test_plan item 8, in-scope half): two
    real browser tabs on ONE project — the second tab submits while the
    first tab's turn is still streaming. Hunts the SILENT-LOSS class: a
    user types into tab 2, nothing visibly happens, and the message
    evaporates. Acceptable outcomes: the second turn runs (before/after),
    or a VISIBLE refusal/queue notice. Also asserts cross-tab convergence:
    after reload both tabs render the same thread truth. Markers computed
    (51*67, 83*59) so needles can't come from the prompts."""
    m1, m2 = 51 * 67, 83 * 59                    # 3417, 4897
    box1 = page.locator("textarea").first
    box1.wait_for(state="visible", timeout=15_000)
    ctx2 = page.context.browser.new_context()
    try:
        page2 = ctx2.new_page()
        page2.goto(BASE["url"], wait_until="domcontentloaded")
        _enter_project(page2)
        box2 = page2.locator("textarea").first
        box1.fill("Run a quick local python step that sleeps 20 seconds and "
                  "then computes and prints 51*67. Report the number.")
        box1.press("Enter")
        time.sleep(4)                            # tab-1 stream is live
        shot(page2, "two_tabs_tab2_during_stream")
        tab2_enabled = False
        try:
            tab2_enabled = box2.is_enabled()
        except Exception:  # noqa: BLE001
            pass
        try:
            box2.fill("Compute 83*59 with a quick local python step and "
                      "report the number.")
            box2.press("Enter")
        except Exception:  # noqa: BLE001
            pass
        print(f"    [obs] tab-2 composer enabled during tab-1 stream: "
              f"{tab2_enabled}")
        deadline = time.time() + 300             # both tabs settle
        while time.time() < deadline:
            try:
                if (page.locator(".composer__stop").count() == 0
                        and page2.locator(".composer__stop").count() == 0):
                    break
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)
        time.sleep(5)
        # THREAD TRUTH from the pages, not from `tid`: the browser lands on
        # the project's DEFAULT thread, not the API-created scenario thread
        # — first live run asserted against a thread nobody typed into
        # (both markers were in the real thread; checks read the empty one)
        pre_reload = ""
        try:
            pre_reload = page.locator("body").inner_text().replace(",", "")
        except Exception:  # noqa: BLE001
            pass
        ran1 = str(m1) in pre_reload
        ran2 = str(m2) in pre_reload
        body2 = ""
        try:
            body2 = page2.locator("body").inner_text()
        except Exception:  # noqa: BLE001
            pass
        # review F2: "wait"/"error" appear in ordinary page chrome — a
        # silently-lost message would false-pass. Only SPECIFIC concurrency
        # notices count as "the user was told".
        notice2 = any(w in body2.lower() for w in
                      ("another turn", "in progress", "busy", "queued",
                       "turn is running", "already running"))
        shot(page2, "two_tabs_tab2_after")
        # convergence: reload BOTH tabs — same thread truth in each
        page.reload(wait_until="domcontentloaded"); _enter_project(page)
        page2.reload(wait_until="domcontentloaded"); _enter_project(page2)
        time.sleep(5)
        b1 = page.locator("body").inner_text().replace(",", "")
        b2 = page2.locator("body").inner_text().replace(",", "")
        shot(page, "two_tabs_tab1_reloaded")
        # review F4: boolean-equality counted mutual TOTAL LOSS as
        # "converged". Tab-1's marker must be PRESENT in both tabs (its
        # turn indisputably ran); m2's presence must agree across tabs.
        converged = (str(m1) in b1) and (str(m1) in b2) and \
                    ((str(m2) in b1) == (str(m2) in b2))
        # dup guard (found live: tab-2's Enter landed TWICE — two user rows,
        # two overlapping assistant turns whose tool calls interrupted each
        # other): the tab-2 prompt must appear exactly once in the thread
        dup2 = b1.count("Compute 83*59 with a quick local python step") > 1
        return [
            ("tab-1 turn completed with the true number", ran1),
            ("tab-2 not silently lost (ran OR visible notice)",
             ran2 or notice2),
            ("tab-2 message not duplicated (single user row)", not dup2),
            ("no crash banner in either tab",
             "couldn't be displayed" not in b1 + b2
             and "couldn’t be displayed" not in b1 + b2),
            ("tabs converge after reload (same thread truth)", converged),
        ]
    finally:
        try:
            ctx2.close()
        except Exception:  # noqa: BLE001
            pass


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
    if only:
        known = {name for name, _ in UI_SCENARIOS}
        unknown = only - known
        if unknown:
            sys.exit(f"[ui] unknown scenario(s): {', '.join(sorted(unknown))}"
                     f" — known: {', '.join(sorted(known))}")
    base = _start_server()
    BASE["url"] = base
    _register_hpc()
    print(f"[ui] serving at {base}\n[ui] shots: {SHOTS}")

    import httpx
    from playwright.sync_api import sync_playwright
    try:
        with httpx.Client(base_url=base, timeout=60) as api, sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(base, wait_until="domcontentloaded")
            for name, fn in UI_SCENARIOS:
                if only and name not in only:
                    continue
                run_ui_scenario(page, api, name, fn)
            browser.close()
    finally:
        # leave no fixture residue for the next runner (the sibling runners
        # clean up in finally; this one didn't — recheck finding)
        if HPC["ok"]:
            try:
                from core.compute import adapter as ad
                ad.get_compute().sync_call("site_unregister", "hpc")
                print("[cleanup] hpc site unregistered")
            except Exception as e:  # noqa: BLE001
                print(f"[cleanup] unregister: {e}")

    if not RESULTS:
        sys.exit("[ui] zero scenarios ran — refusing a vacuous ALL PASS")
    print("\nUI STUDY:", "ALL PASS" if all(ok for _, ok in RESULTS)
          else "FAILURES: " + ", ".join(n for n, ok in RESULTS if not ok))
    sys.exit(0 if all(ok for _, ok in RESULTS) else 1)


if __name__ == "__main__":
    main()
