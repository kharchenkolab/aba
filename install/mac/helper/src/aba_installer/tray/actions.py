"""Tray action handlers — Start / Stop / Restart / Open / Check updates /
Show logs / Kickstart helper.

Each handler talks to the helper service over loopback (no privilege required)
or, for ``kickstart_helper``, to ``launchctl`` directly. Returns a uniform
``ActionResult`` the menu can show as a one-line toast or status message —
specifically including a *helper offline* error mode that points the user at
the right next move (kickstart the LaunchAgent) rather than just saying
"failed"."""
from __future__ import annotations
import json
import os
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


# The helper LaunchAgent's label — kept in sync with launchagent.LABEL.
# Don't import that module here; we want this file to stay importable
# inside the tray process before the full helper package finishes loading.
_HELPER_LABEL = "com.kharchenkolab.aba.helper"

_BACKEND_URL = "http://127.0.0.1:8000/"


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str = ""              # one-line summary the menu can toast
    applied_on_next_turn: bool = False  # set by set_model when ABA_MODEL changed


# ─── HTTP helper ──────────────────────────────────────────────────────────
def _post(path: str, *, port: int, urlopen: Callable,
          timeout_s: float = 30.0) -> tuple[bool, Any, Optional[str]]:
    """POST <path> with no body. Returns (ok, parsed_response, error_message).
    The third tuple element is the *user-facing* error message — not just an
    exception repr — so handlers don't have to rebuild it."""
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method="POST",
                                 data=b"", headers={"Content-Length": "0"})
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        return False, None, f"helper rejected {path} (HTTP {e.code})"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False, None, ("Helper offline — start it from the menu "
                             "('Start helper…') or System Settings → "
                             "Login Items.")
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return False, None, f"helper returned non-JSON from {path}"
    return True, parsed, None


# ─── start / stop / restart ──────────────────────────────────────────────
def start(*, port: int,
          urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    ok, parsed, err = _post("/api/start", port=port, urlopen=urlopen)
    if not ok:
        return ActionResult(False, err or "")
    msg = parsed.get("stdout") or "Started." if isinstance(parsed, dict) else "Started."
    return ActionResult(True, str(msg).strip())


def stop(*, port: int,
         urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    ok, parsed, err = _post("/api/stop", port=port, urlopen=urlopen)
    if not ok:
        return ActionResult(False, err or "")
    return ActionResult(True, "Stopped.")


def restart(*, port: int,
            urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    """Stop, then start. Don't try to start something that just refused to
    stop — the user needs to see what's wrong with stop, not have it masked
    by a downstream start error."""
    s = stop(port=port, urlopen=urlopen)
    if not s.ok:
        return ActionResult(False, f"stop failed: {s.message}")
    return start(port=port, urlopen=urlopen)


# ─── open in browser ─────────────────────────────────────────────────────
def open_abc_browser(*, open_url: Callable[[str], None]) -> ActionResult:
    """Open the ABA app (backend SPA) at :8000 in the default browser."""
    open_url(_BACKEND_URL)
    return ActionResult(True, _BACKEND_URL)


def _control_page_ok(*, port: int, urlopen: Callable,
                     timeout_s: float = 1.5) -> bool:
    """True iff the helper's CONTROL PAGE ('/' — what we forward the user
    to) returns 2xx within timeout_s. Probing '/' rather than '/api/status'
    is deliberate: the 2026-06-11 bug shape had /api/status returning 200
    while '/' threw 500 from a stale StaticFiles path. Probing the actual
    target URL is the only honest health check."""
    url = f"http://127.0.0.1:{port}/"
    try:
        with urlopen(urllib.request.Request(url), timeout=timeout_s) as resp:
            resp.read()
            status = getattr(resp, "status", None) or resp.getcode()
            return 200 <= int(status) < 300
    except urllib.error.HTTPError:
        return False                     # any 4xx/5xx is "not OK"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return False


def check_updates(*, port: int,
                  open_url: Callable[[str], None],
                  open_path: Callable[[Path], None] = None,
                  notify: Callable[[str, str, str], None] = None,
                  urlopen: Callable = urllib.request.urlopen,
                  run: Callable = subprocess.run,
                  sleep: Callable[[float], None] = None,
                  thread_factory: Callable = None,
                  ) -> ActionResult:
    """One-stop update flow. Tries every path that might work, in order,
    and falls through silently — the user clicks ONE menu item and the
    tray handles every contingency:

      1. Probe '/' on the helper. If 2xx → open the browser Control
         page; the user watches the SSE step list + live log there.
      2. If '/' is broken (500 from a stale dist, connect-refused,
         etc.), launchctl kickstart -k the helper, wait ~5s, re-probe.
         A stale-dist 500 self-heals across a restart because the new
         process picks up the current package layout.
      3. If '/' is STILL bad after the kickstart, fall back to the
         inline update path — same playbook (update.yml) run in the
         tray process, streaming to ~/.aba/logs/tray-update.log,
         opening that file in the OS's default .log handler so the
         user can watch. A notification fires on done/fail.

    The user never sees a backup button; the cascading fallbacks are
    invisible unless they want to look at the notification stream.

    Pre-2026-06-11 the only path was 'open the browser'. A helper that
    answered /api/status but threw 500 on '/' (stale StaticFiles path
    after a package layout change) made the user click straight into
    Internal Server Error with no recovery affordance.
    """
    notify = notify or (lambda *a: None)
    url = f"http://127.0.0.1:{port}/"

    # Path 1: control page is healthy.
    if _control_page_ok(port=port, urlopen=urlopen):
        open_url(url)
        return ActionResult(True, url)

    # Path 2: kickstart the helper and re-probe.
    notify("ABA update", "Reviving helper…",
           "Control page wasn't responding; restarting it.")
    ks = kickstart_helper(run=run)
    if ks.ok:
        import time as _time
        _sleep = sleep or _time.sleep
        for _ in range(10):                   # up to ~5s
            _sleep(0.5)
            if _control_page_ok(port=port, urlopen=urlopen):
                open_url(url)
                return ActionResult(True, url)

    # Path 3: inline update as last resort. Bypasses the helper entirely.
    notify("ABA update", "Running inline",
           "Helper still unhealthy — updating in the tray. "
           "Watch ~/.aba/logs/tray-update.log.")
    _op = open_path or (lambda _p: None)
    return update_inline(open_path=_op, notify=notify,
                         thread_factory=thread_factory, run=run)


# ─── show logs ───────────────────────────────────────────────────────────
def show_logs(*, port: int, log_dir: Path,
              urlopen: Callable = urllib.request.urlopen,
              open_path: Callable[[Path], None]) -> ActionResult:
    """Pull recent backend log lines from the helper, write them to a
    ``log_dir``-relative file, and open that file with the OS's default
    handler (TextEdit, Console.app, or whatever the user mapped to .log).

    Writing to a file rather than streaming into a rumps window keeps the
    tray's GUI surface tiny — and lets the user keep the log open in a real
    window while they do something else."""
    url = f"http://127.0.0.1:{port}/api/logs?tail=200"
    req = urllib.request.Request(url)
    try:
        with urlopen(req, timeout=5.0) as resp:
            body = resp.read()
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ActionResult(False, "Helper offline — can't read logs.")
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return ActionResult(False, "Garbage response from /api/logs.")
    lines = parsed.get("lines") or [] if isinstance(parsed, dict) else []
    log_dir.mkdir(parents=True, exist_ok=True)
    # Stable name (we overwrite per click — users don't want a directory full
    # of one-off log dumps; the helper's own backend.log is the persistent record).
    dest = log_dir / "tray-backend-tail.log"
    dest.write_text("\n".join(lines) + "\n")
    open_path(dest)
    return ActionResult(True, str(dest))


# ─── kickstart the helper LaunchAgent ────────────────────────────────────
def _gui_target() -> str:
    """``gui/<uid>/<label>`` is the modern launchctl target. Use it; older
    macOS versions accept it too."""
    return f"gui/{os.getuid()}/{_HELPER_LABEL}"


def set_model(*, model_id: str, port: int,
              urlopen: Callable = urllib.request.urlopen) -> ActionResult:
    """POST /api/auth/model with the chosen id. The helper persists it to
    config.env and tells us whether a restart is needed; we propagate
    restart_required so the tray can surface a notification."""
    url = f"http://127.0.0.1:{port}/api/auth/model"
    body = json.dumps({"model": model_id}).encode()
    req = urllib.request.Request(url, method="POST", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10.0) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return ActionResult(False, f"helper rejected model change (HTTP {e.code})")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        return ActionResult(False, ("Helper offline — start it from the menu "
                                    "('Start helper…') or System Settings → "
                                    "Login Items."))
    except (json.JSONDecodeError, ValueError):
        return ActionResult(False, "helper returned non-JSON from /api/auth/model")
    return ActionResult(
        ok=bool(payload.get("ok")),
        message=str(payload.get("model") or model_id),
        applied_on_next_turn=bool(payload.get("applied_on_next_turn")),
    )


# ─── inline update (no helper / no browser) ──────────────────────────────
# Module-level guard so a double-click on 'Update now…' doesn't spawn two
# concurrent playbook runs. The flag is owned by `update_inline`; reset
# by the worker on done OR failure.
_INLINE_UPDATE_LOCK = threading.Lock()
_INLINE_UPDATE_INFLIGHT = False


def _inline_log_path() -> Path:
    """Where the inline updater streams its events. One stable file per
    run (overwritten each invocation) so the user can keep TextEdit open
    on it across attempts."""
    from aba_installer.paths import logs_dir
    return logs_dir() / "tray-update.log"


def update_inline(*,
                  open_path: Callable[[Path], None],
                  notify: Callable[[str, str, str], None] = None,
                  log_path: Optional[Path] = None,
                  thread_factory: Callable = None,
                  run: Callable = subprocess.run,
                  ) -> ActionResult:
    """Run the update playbook DIRECTLY in the tray process — no helper
    HTTP, no browser. Streams one line per executor event to
    ``$ABA_HOME/logs/tray-update.log`` and opens that file so the user
    can watch in TextEdit. Fires a rumps notification on done / fail.

    The point: when the helper is wedged or refusing to start, the
    update path that would FIX it (which includes a fresh
    ``pull-aba``, env refresh, and bounce-backend) becomes the very
    thing you can't run. This bypass closes that loop. Same playbook
    the browser Control page uses (update.yml), same Executor — only
    the front-end is different.

    Implementation:
      - Refuses with a clear error if another inline update is in flight.
      - Spawns a daemon thread (the run takes minutes; the tray must
        stay responsive). Notification fires when the thread finishes.
      - Worker writes one human-readable line per executor event AND
        flushes after each, so a `tail -f` (or a manual reload in
        TextEdit) shows progress in real time.

    `thread_factory` exists for tests — pass a lambda that records the
    target function and runs it synchronously."""
    import time
    from aba_installer.playbook import Executor, load_playbook

    global _INLINE_UPDATE_INFLIGHT
    with _INLINE_UPDATE_LOCK:
        if _INLINE_UPDATE_INFLIGHT:
            return ActionResult(
                False,
                "An inline update is already running — watch "
                f"{_inline_log_path()}.",
            )
        _INLINE_UPDATE_INFLIGHT = True

    notify = notify or (lambda *a: None)
    log_path = log_path or _inline_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Find update.yml in the installed helper package. Mirrors the
    # control endpoint's resolution (_playbook_path) without importing
    # FastAPI just for that.
    pb_path = Path(__file__).resolve().parent.parent / "update.yml"
    if not pb_path.exists():
        with _INLINE_UPDATE_LOCK:
            _INLINE_UPDATE_INFLIGHT = False
        return ActionResult(
            False, f"update.yml not found at {pb_path}")
    pb = load_playbook(pb_path)

    def _worker():
        global _INLINE_UPDATE_INFLIGHT
        ok = False
        last_step = ""
        try:
            with open(log_path, "w", buffering=1) as f:
                f.write(f"=== ABA inline update started "
                        f"{time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(f"playbook: {pb_path}\n")
                f.write(f"steps: {', '.join(s.id for s in pb.steps)}\n\n")

                def on_event(name: str, payload: dict) -> None:
                    # Compact one-line render. The browser Control page
                    # has a richer UI; this is the 'I can tail this'
                    # alternative.
                    nonlocal last_step
                    if name == "step_start":
                        last_step = str(payload.get("title")
                                        or payload.get("step_id") or "")
                        f.write(f"\n>>> {last_step}\n")
                    elif name == "command_output":
                        line = str(payload.get("line") or "")
                        f.write(f"    {line}\n")
                    elif name == "command_end":
                        rc = payload.get("returncode")
                        if rc not in (None, 0):
                            f.write(f"    [command exit {rc}]\n")
                    elif name == "step_end":
                        ok_step = payload.get("ok")
                        f.write(f"<<< {last_step}: "
                                f"{'OK' if ok_step else 'FAILED'}\n")
                    else:
                        f.write(f"[{name}] {payload}\n")
                    f.flush()

                ex = Executor(pb, on_event=on_event)
                results = ex.run_all()
                ok = all(r.ok for r in results)
                f.write(f"\n=== {'DONE OK' if ok else 'DONE FAILED'} ===\n")
        except Exception as e:                                   # noqa: BLE001
            try:
                with open(log_path, "a") as f:
                    f.write(f"\n!!! tray-update exception: {e!r}\n")
            except Exception:                                    # noqa: BLE001
                pass
            ok = False
        finally:
            with _INLINE_UPDATE_LOCK:
                _INLINE_UPDATE_INFLIGHT = False
            # On success, kickstart the helper so any stale-dist '/' 500
            # from a layout swap in the run we just did self-heals. The
            # browser Control page works on next click without a manual
            # restart. Best-effort: a failure here doesn't change `ok`.
            if ok:
                try:
                    kickstart_helper(run=run)
                except Exception:                                # noqa: BLE001
                    pass
            try:
                notify(
                    "ABA update",
                    "OK" if ok else "Failed",
                    f"See {log_path}",
                )
            except Exception:                                    # noqa: BLE001
                pass

    # Open the log immediately so the user can watch.
    try:
        open_path(log_path)
    except Exception:  # noqa: BLE001 — opening is best-effort; the run still goes
        pass

    factory = thread_factory or (lambda target: threading.Thread(
        target=target, daemon=True, name="aba-tray-inline-update"))
    t = factory(_worker)
    t.start()
    return ActionResult(True, f"Update started — watch {log_path}.")


def kickstart_helper(*, run: Callable = subprocess.run) -> ActionResult:
    """``launchctl kickstart`` the helper LaunchAgent. Used when the tray
    poller reports ``helper_offline`` and the user clicks 'Start helper…'.

    No privilege escalation — user-level LaunchAgents take ``gui/<uid>/...``
    and need no admin."""
    argv = ["launchctl", "kickstart", "-k", _gui_target()]
    try:
        proc = run(argv, capture_output=True, text=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        return ActionResult(False, f"launchctl error: {e}")
    if proc.returncode != 0:
        # The most common failure here is "Operation not permitted" when the
        # plist isn't registered (the helper LaunchAgent was never installed).
        return ActionResult(False, (proc.stderr or proc.stdout or "").strip()
                            or f"launchctl exited {proc.returncode}")
    return ActionResult(True, "Helper started.")
