"""Live consumption-surface audit — point the surface-parity oracle at a
RUNNING server and walk every project's runs.

The scenario sweep applies the oracle to fresh synthetic sessions; this tool
applies the SAME oracle to a real deployment's accumulated projects — the
"first click after coming back" experience: every file the listings advertise
must open or refuse honestly, viewer lookups must see what listings show,
downloads must not dead-link.

Usage:
    python regtest/harness/live_audit.py [base_url] [--project PID]

Default base_url http://127.0.0.1:8000. Without --project it audits EVERY
project (note: it POSTs /api/projects/{pid}/open to bind each in turn — on a
shared server this flips the active project; it restores the initially-open
project at the end when discoverable). Exit 0 = parity holds everywhere;
exit 1 = failures (printed one per line, prefixed by project id).
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from surfaces import surface_parity_failures  # noqa: E402
from transport import transport_truth  # noqa: E402

try:
    import requests as _rq
except ImportError:  # pragma: no cover — stdlib fallback, deploy envs lack requests
    _rq = None


class _Resp:
    def __init__(self, status: int, body: bytes):
        self.status_code = status
        self.content = body
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        import json as _json
        return _json.loads(self.text or "null")


class _Client:
    """requests when available, urllib otherwise — same .get/.post surface."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _urllib(self, url: str, method: str):
        import urllib.request
        import urllib.error
        req = urllib.request.Request(self.base + url, method=method,
                                     data=b"" if method == "POST" else None)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return _Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read() or b"")

    def get(self, url: str):
        if _rq:
            return _rq.get(self.base + url, timeout=60)
        return self._urllib(url, "GET")

    def post(self, url: str, **kw):
        if _rq:
            return _rq.post(self.base + url, timeout=60, **kw)
        return self._urllib(url, "POST")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    base = "http://127.0.0.1:8000"
    only = None
    while args:
        a = args.pop(0)
        if a == "--project":
            only = args.pop(0)
        else:
            base = a
    c = _Client(base)

    h = c.get("/api/health")
    if h.status_code != 200:
        print(f"server not healthy at {base}: {h.status_code}")
        return 2

    try:
        pj = c.get("/api/projects").json()
        rows = pj if isinstance(pj, list) else pj.get("projects", [])
        projects = [p.get("id") for p in rows]
        # remember the currently-open project so the sweep can restore it —
        # opening each project in turn mutates the server's active binding
        initial = next((p.get("id") for p in rows
                        if p.get("current") or p.get("active") or p.get("open")),
                       None)
    except Exception as e:  # noqa: BLE001
        print(f"cannot list projects: {e}")
        return 2
    if only:
        projects = [only]

    all_fails: list[str] = []
    for pid in [p for p in projects if p]:
        try:
            c.post(f"/api/projects/{pid}/open")
        except Exception as e:  # noqa: BLE001
            all_fails.append(f"{pid}: open_failed:{e}")
            continue
        fails = surface_parity_failures(c, pid, max_fetches=60)
        # mechanism truth alongside surface truth: recent exec records must
        # say the SUBSTRATE ran them — a deployment misconfigured onto a
        # legacy lane looks identical on every outcome surface, and only
        # this check catches it (the lesson of the kernel-transport gap)
        tt = transport_truth(c, pid, max_runs=8)
        fails += tt["failures"]
        for f in fails:
            all_fails.append(f"{pid}: {f}")
        print(f"[{pid}] {'OK' if not fails else f'{len(fails)} failure(s)'}"
              f" (execs checked: {tt['checked']})")

    if initial and not only:
        try:
            c.post(f"/api/projects/{initial}/open")
            print(f"(restored active project {initial})")
        except Exception:  # noqa: BLE001 — restore is best-effort
            pass

    if all_fails:
        print("\n== surface-parity failures ==")
        for f in all_fails:
            print("  " + f)
        return 1
    print("\nall projects: every advertised surface answers honestly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
