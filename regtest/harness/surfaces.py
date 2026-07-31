"""Consumption-surface parity oracle — "can a person actually open what was
computed?"

Why this exists: the sweep's oracles historically stopped at the substrate —
scenarios verified that steps computed right answers and recorded right rows
(entities, retention index, job results), and passed, while every USER-FACING
consumption surface (file serving, listings, viewer lookup, entity download)
failed, because each surface independently equated "exists" with "exists on
the controller's disk". A fully green sweep and a fully broken experience were
consistent with each other. This module closes that gap as a STANDING
post-condition: after a scenario runs, walk the real HTTP surfaces the product
renders and assert PRESENTATION PARITY with recorded truth —

  * every file row the run durability listing advertises must ANSWER at its
    URL: 200 with bytes, or an HONEST refusal (413 that names where the bytes
    live / why) — never a dead link (404/5xx) for a file the listing claims
    exists;
  * a `retained` row must carry a live URL — kept bytes are always reachable
    or honestly refused, never silently unlinked;
  * every produced artifact with a served URL must stream non-empty bytes
    (the judge used to fetch these and silently swallow failures);
  * an entity download must 200 or refuse honestly — never "missing on disk"
    while the durability view calls the same bytes kept;
  * the viewer lookup must be able to SEE a produced output whose extension
    an external viewer claims (a lookup that can't find what the run listing
    shows is the fabricated-placeholder bug class).

Driver-agnostic: `client` is anything with .get(path) → response
(.status_code, .json(), .content) — the sweep's in-process TestClient or a
thin wrapper over `requests` against a live server. Never raises; returns a
list of "surface:<class>:<detail>" failure strings (empty = parity holds).
"""
from __future__ import annotations

_KNOWN_STATES = {"retained", "saving", "in-store", "at-risk",
                 "in-sandbox", "cleared", "unknown"}

# An honest refusal names a constraint; a dead link names nothing.
_HONEST_STATUSES = {200, 413}


def _get(client, url):
    try:
        return client.get(url)
    except Exception as e:  # noqa: BLE001 — a crashed route is a surface failure
        class _R:  # minimal response shim so callers report it uniformly
            status_code = 599
            content = b""
            text = f"{type(e).__name__}: {e}"

            @staticmethod
            def json():
                return {}
        return _R()


def _viewer_extensions(client) -> list[str]:
    """Extensions any registered external viewer claims (dynamic — no
    domain knowledge baked in here)."""
    try:
        reg = _get(client, "/api/viewers/registry").json()
        return sorted({e.lower() for v in reg if isinstance(v, dict)
                       for e in (v.get("extensions") or [])})
    except Exception:  # noqa: BLE001
        return []


def surface_parity_failures(client, pid: str, *, run_ids=None,
                            max_fetches: int = 40) -> list[str]:
    """Walk the consumption surfaces for the project's runs and return parity
    failures. `run_ids` bounds the walk (default: every active run entity);
    `max_fetches` bounds byte-serving round-trips so the oracle stays cheap."""
    fails: list[str] = []
    budget = [max_fetches]

    try:
        ents = _get(client, f"/api/entities?project_id={pid}&include_archived=false").json()
        ents = ents if isinstance(ents, list) else ents.get("entities", [])
    except Exception as e:  # noqa: BLE001
        return [f"surface:entities_unreadable:{e}"]
    if run_ids is None:
        run_ids = [e["id"] for e in ents if e.get("type") == "analysis"
                   and e.get("status") == "active"]

    def _fetch_ok(url: str, ctx: str, expect_bytes: bool = True) -> None:
        if budget[0] <= 0:
            return
        budget[0] -= 1
        r = _get(client, url)
        if r.status_code == 200:
            if expect_bytes and not r.content:
                fails.append(f"surface:empty_bytes:{ctx} ({url})")
            return
        if r.status_code in _HONEST_STATUSES:
            return
        body = getattr(r, "text", "")[:120]
        fails.append(f"surface:dead_link:{ctx} {url} -> {r.status_code} {body!r}")

    vexts = _viewer_extensions(client)

    for rid in run_ids:
        # 1) the LIST surface: recorded truth must surface coherently…
        dv = _get(client, f"/api/runs/{rid}/durable?flat=1")
        if dv.status_code != 200:
            fails.append(f"surface:durable_view:{rid} -> {dv.status_code}")
            continue
        files = (dv.json() or {}).get("files") or []
        for f in files:
            rel, state, url = f.get("rel"), f.get("state"), f.get("url")
            if state not in _KNOWN_STATES:
                fails.append(f"surface:unknown_state:{rid}/{rel} state={state!r}")
            if state == "retained" and not url:
                fails.append(f"surface:retained_unlinked:{rid}/{rel} "
                             f"(kept bytes with no URL)")
            # …and 2) every advertised link must ANSWER (serve surface).
            if url:
                _fetch_ok(url, f"{rid}/{rel}",
                          expect_bytes=bool(f.get("bytes")))
            # 5) the viewer LOOKUP must see what the listing shows.
            base = (rel or "").rsplit("/", 1)[-1]
            if any(base.lower().endswith(x) for x in vexts) and budget[0] > 0:
                budget[0] -= 1
                vr = _get(client, f"/api/viewers/for?path={base}")
                if vr.status_code not in (200,):
                    fails.append(f"surface:viewer_blind:{rid}/{rel} -> "
                                 f"{vr.status_code}")
        # 3) produced artifacts with served URLs must stream.
        arts = _get(client, f"/api/runs/{rid}/artifacts")
        if arts.status_code == 200:
            aj = arts.json() or []
            if isinstance(aj, dict):
                aj = aj.get("artifacts") or []
            for a in aj[:10]:
                if isinstance(a, dict) and (a.get("url") or "").startswith("/artifacts/"):
                    _fetch_ok(a["url"], f"artifact:{rid}/{a.get('original_name')}")

    # 4) entity downloads: produced/pinned entities with an artifact must
    # serve or refuse honestly (never "missing on disk" for durable bytes).
    for e in ents:
        if e.get("status") != "active" or not e.get("artifact_path"):
            continue
        if e.get("type") == "analysis" or budget[0] <= 0:
            continue
        # context is the opaque entity id only — failure strings land in
        # scorecards/reports and must not carry user prose (titles)
        _fetch_ok(f"/api/entities/{e['id']}/download", f"entity:{e['id']}")

    return fails
