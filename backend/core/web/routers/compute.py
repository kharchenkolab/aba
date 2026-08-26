"""Compute API — Settings → Compute (misc/compute_settings.md §7).

The curated projection over the WeftAdapter SitePort — NOT a raw tool
passthrough (that's weft-ui's facade; aba's discipline is one small router
per surface). Flow endpoints mirror the tab's moments: access (preflight /
hostkey / keysetup) → probe (register_site probe_only + the pure §5.4
proposal) → connect (register + weft-sites.yaml write + background queue
verification) → manage (edit / disconnect / free up).

Two contracts enforced here rather than in the UI:
  * no password ever crosses this API (keysetup returns a command for the
    user's own terminal; there is no secret-bearing request model);
  * weft's sqlite stays canonical while every add/edit/forget mirrors into
    weft-sites.yaml so deployments stay declarative (§3b).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.compute.errors import ComputeError

router = APIRouter()

# background queue-verification tasks + last outcomes, by site name
_verify_tasks: dict[str, asyncio.Task] = {}
_verify_state: dict[str, dict] = {}


def _adapter():
    from core.compute.adapter import get_compute
    try:
        return get_compute()
    except ComputeError as e:
        raise HTTPException(503, e.to_payload())


def _http(e: ComputeError) -> HTTPException:
    status = 409 if e.code == "state.conflict" else 502
    return HTTPException(status, e.to_payload())


def _broadcast(site: str, phase: str, **kw) -> None:
    from core.runtime import notifications, wire
    notifications.broadcast(wire.compute(site=site, phase=phase, **kw))


# ── substrate event → Console envelope mapping (pure; unit-tested) ────────────
# Family prefix → console category. Kinds outside this table are dropped —
# a new weft family stays invisible until deliberately mapped (silence is a
# mapping decision, not an accident: test_console_events pins the table).
_FAMILY_CATEGORY = {
    "transfer": "data", "data": "data", "retain": "data", "gc": "data",
    "collect": "data", "cas": "data",
    "job": "run", "array": "run", "run": "run", "kernel": "run",
    "env": "env", "realize": "env", "overlay": "env", "session": "env",
    "bundle": "env",
    "site": "compute", "bootstrap": "compute", "poller": "compute",
    "service": "serve",
}
# "died" is an UNPLANNED termination (weft's poller detects it and attaches
# cause/exit_code/killing_block/suggestion) — distinct from the clean
# `kernel.stopped`. It was landing as `info`, so a kernel dying in a loop
# rendered the same as a kernel starting normally.
_ERROR_MARKS = ("failed", "error", "unreachable", "lost", "integrity", "bad",
                "died")
_WARN_MARKS = ("warning", "skipped", "unverified", "unportable", "fallback",
               "missing", "deferred", "cancelled", "interrupted")


# session id → site, learned from events that carry both (session.started
# does). weft's session.* lifecycle events (ensure_attempt/ensure_done/
# installed — the ensure_capability path) name the SESSION but not the SITE,
# so their Console rows rendered without the local/remote chip every other
# row gets — and a capability install is one of the most site-specific things
# the console shows (live feedback 2026-08-09). Backfill from the mapping;
# bounded so a long-lived server cannot grow it without limit.
_SESSION_SITE: dict = {}
_SESSION_SITE_CAP = 512


def console_event_for(ev: dict) -> dict | None:
    """Map one substrate event (`{kind, site?, job_id?, **payload}`) to a
    `console` wire payload, or None when the family isn't console-worthy."""
    from core.runtime import wire
    kind = str(ev.get("kind") or "")
    family = kind.split(".", 1)[0]
    category = _FAMILY_CATEGORY.get(family)
    if category is None:
        return None
    sess = ev.get("session")
    if sess:
        if ev.get("site"):
            if len(_SESSION_SITE) >= _SESSION_SITE_CAP:
                _SESSION_SITE.clear()          # tiny map; wholesale reset is fine
            _SESSION_SITE[str(sess)] = str(ev["site"])
        elif str(sess) in _SESSION_SITE:
            ev = {**ev, "site": _SESSION_SITE[str(sess)]}
    tail = kind.split(".", 1)[1] if "." in kind else ""
    severity = ("error" if any(m in tail for m in _ERROR_MARKS)
                else "warn" if any(m in tail for m in _WARN_MARKS)
                else "info")
    wall = ev.get("wall_s")
    exit_code = ev.get("exit_code")
    summary = " · ".join(
        str(v) for v in (ev.get("step"), ev.get("note"), ev.get("src")) if v) or None
    detail = {k: v for k, v in ev.items()
              if k not in ("kind", "site") and v is not None} or None
    fields = {
        "severity": severity,
        "site": str(ev["site"]) if ev.get("site") else None,
        "summary": summary,
        "dur_ms": int(float(wall) * 1000) if isinstance(wall, (int, float)) else None,
        "bytes": ev.get("bytes_total") if isinstance(ev.get("bytes_total"), int) else None,
        "status": str(exit_code) if exit_code is not None else None,
        "ref": str(ev["job_id"]) if ev.get("job_id") else None,
        "detail": detail,
    }
    return wire.console(category=category, verb=kind,
                        **{k: v for k, v in fields.items() if v is not None})


def wire_event_relay() -> bool:
    """Relay weft's event feed onto the notification bus. Two consumers, two
    envelopes: bootstrap./site. kinds keep the legacy `compute` event (the
    Settings → Compute tab's live-refresh contract), and every mapped family
    ALSO lands as a structured `console` event (the Console drawer's feed).
    Called from lifespan right after the substrate configures; False when
    offline."""
    from core.compute.adapter import get_compute
    from core.runtime import notifications
    try:
        comp = get_compute()
    except ComputeError:
        return False

    def _cb(ev: dict) -> None:  # runs on weft's emitting thread
        kind = str(ev.get("kind") or "")
        if kind.startswith(("bootstrap.", "site.")):
            _broadcast(str(ev.get("site") or ""), kind,
                       step=ev.get("step"), note=ev.get("note"))
        payload = console_event_for(ev)
        if payload is not None:
            notifications.broadcast(payload)

    comp.subscribe_events(_cb)
    return True


# ── request models ────────────────────────────────────────────────────────────

class Target(BaseModel):
    dest: str
    port: Optional[int] = None
    ssh_opts: list[str] = []


class HostKey(BaseModel):
    line: str


class Proposal(BaseModel):
    """The (possibly user-tweaked) §5.4 proposal coming back for Add."""
    name: str
    kind: str                       # local | ssh | slurm
    use_for: list[str]
    working: dict                   # {root, ...}
    long_term: list[dict] = []      # [{path, stable}]
    contract: str = "detached"
    partitions: list[dict] = []
    account: Optional[str] = None
    notes: list[str] = []           # free-text policy guidance (weft surfaces
                                    # it in every submit plan)
    durable: bool = False           # the working root's storage lasts (keeps
                                    # are retained in place, so this is the
                                    # keeps' durability)
    durable_path: Optional[str] = None   # topology C: durable storage at a
                                         # separate path on the node


class ConnectRequest(Target):
    proposal: Proposal


class SiteEdit(BaseModel):
    use_for: Optional[list[str]] = None
    long_term: Optional[list[dict]] = None
    notes: Optional[list[str]] = None
    # moving the working space re-registers the site there; weft bootstraps
    # anew at the new root (envs/caches rebuild) — the old root is NOT
    # cleaned automatically (Free up first)
    working_root: Optional[str] = None
    durable: Optional[bool] = None
    durable_path: Optional[str] = None   # "" clears it


class GcRequest(BaseModel):
    confirm: bool = False


# ── status / discovery ───────────────────────────────────────────────────────

@router.get("/api/compute/status")
def compute_status() -> dict:
    """Substrate health for the tab's offline banner, plus whether users may
    manage sites themselves (shared installs declare their sites in
    weft-sites.yaml and set self_service: false — the tab goes read-only)."""
    from core.compute import sites_config
    from core.compute.adapter import status
    return {**status(), "self_service": sites_config.self_service()}


def _require_self_service() -> None:
    from core.compute import sites_config
    if not sites_config.self_service():
        raise HTTPException(403, {
            "error": "self_service_disabled",
            "detail": "compute sites on this installation are managed by "
                      "the deployment — ask your administrator to change "
                      "machines or storage declarations"})


@router.get("/api/compute/advanced")
def advanced(site: Optional[str] = None) -> dict:
    """The 'Advanced ↗' deep link into the mounted weft-ui (§8) — the same
    workspace with every knob exposed, chat rail hidden. `available: false`
    when weft-ui isn't installed in this deployment."""
    from core.web import weftui
    url = weftui.advanced_url(site)
    return {"available": url is not None, "url": url}


@router.get("/api/compute/hosts")
def saved_hosts() -> dict:
    """~/.ssh/config concrete hosts — the entry screen's saved-host picker."""
    from core.compute import preflight
    return {"hosts": preflight.ssh_config_hosts()}


@router.get("/api/compute/templates")
def compute_templates() -> dict:
    """Deployment-declared connect templates (§5.7 'Your lab uses …').
    Today: $ABA_HOME/compute-templates.yaml written by an admin/installer;
    bundle-scope composition is the planned follow-up (arch doc Known gaps)."""
    from core import config
    path = config.aba_home() / "compute-templates.yaml"
    if not path.exists():
        return {"templates": []}
    try:
        import yaml
        doc = yaml.safe_load(path.read_text()) or {}
        return {"templates": [t for t in (doc.get("templates") or [])
                              if isinstance(t, dict) and t.get("name")]}
    except Exception as e:  # noqa: BLE001 — a broken file lists as empty, loudly
        return {"templates": [], "error": str(e)}


# ── list / detail ────────────────────────────────────────────────────────────

@router.get("/api/compute/sites")
async def list_sites() -> dict:
    """All sites: weft's live summary + capabilities + the aba-side keys and
    (when known) the last background-verification outcome."""
    from core.compute import sites_config
    comp = _adapter()
    try:
        rows = await comp.sites_list()
    except ComputeError as e:
        raise _http(e)
    out = []
    for row in rows:
        name = row.get("name", "")
        entry = dict(row)
        try:
            desc = await comp.sites_describe(name)
            entry["capabilities"] = desc.get("capabilities")
            entry["probed_at"] = desc.get("probed_at")
            # the card's manage affordances (working root, durable, notes) key
            # on config — sites_list rows stopped carrying it upstream, so
            # forward it from describe like capabilities
            entry["config"] = desc.get("config")
        except ComputeError:
            entry["capabilities"] = None
        entry["aba"] = sites_config.aba_keys(name) or (
            {"contract": "shared-fs", "use_for": ["interactive", "background"]}
            if name == "local" else {})
        if name in _verify_state:
            entry["verify"] = _verify_state[name]
        out.append(entry)
    return {"sites": out}


@router.get("/api/compute/sites/{name}")
async def site_detail(name: str) -> dict:
    from core.compute import sites_config
    comp = _adapter()
    try:
        desc = await comp.sites_describe(name)
    except ComputeError as e:
        raise _http(e)
    desc["aba"] = sites_config.aba_keys(name)
    if name in _verify_state:
        desc["verify"] = _verify_state[name]
    return desc


@router.get("/api/compute/sites/{name}/load")
async def site_load(name: str, estimate: bool = False) -> dict:
    """Live load; with ?estimate=1 adds the card's start estimate for a
    modest ask (4 cores / 8 GB / 1 h) — 'if I send something now, when
    does it run?'."""
    comp = _adapter()
    resources = {"cpus": 4, "mem_gb": 8, "walltime_s": 3600} if estimate else None
    try:
        return await comp.site_load(name, resources=resources)
    except ComputeError as e:
        raise _http(e)


@router.get("/api/compute/sites/{name}/footprint")
async def site_footprint(name: str) -> dict:
    comp = _adapter()
    try:
        return await comp.site_footprint(name)
    except ComputeError as e:
        raise _http(e)


# ── the connect flow: access → probe → connect ───────────────────────────────

@router.post("/api/compute/preflight")
async def preflight(t: Target) -> dict:
    _require_self_service()
    """Fast reachability + classified cause; on first contact carries the
    host key fingerprint for the §5.2 confirm card."""
    from core.compute import preflight as pf
    out = await asyncio.to_thread(pf.preflight, t.dest, t.port, t.ssh_opts)
    if out.get("case") == "invalid":
        raise HTTPException(400, out.get("detail", "invalid target"))
    return out


@router.post("/api/compute/hostkey")
async def accept_hostkey(k: HostKey) -> dict:
    _require_self_service()
    """Record a USER-CONFIRMED host key in aba's TOFU store (never the
    user's own ~/.ssh/known_hosts)."""
    from core.compute import preflight as pf
    path = await asyncio.to_thread(pf.accept_hostkey, k.line)
    return {"ok": True, "store": str(path)}


@router.post("/api/compute/keysetup")
async def keysetup(t: Target) -> dict:
    _require_self_service()
    """Dedicated-key setup: generates ~/.ssh/aba_ed25519 if needed and
    returns the exact `ssh-copy-id` line for the user's OWN terminal.
    aba never sees or transports the password."""
    from core.compute import preflight as pf
    out = await asyncio.to_thread(pf.keysetup, t.dest, t.port)
    if not out.get("ok"):
        raise HTTPException(502, out.get("detail", "key generation failed"))
    return out


@router.post("/api/compute/probe")
async def probe(t: Target) -> dict:
    _require_self_service()
    """The §5.3 moment: one preliminary facts call (scheduler kind, shared-fs
    canary, accounts), then weft's own probe via register_site(probe_only)
    — nothing persisted — and the pure §5.4 proposal over the result."""
    from core.compute import inference, preflight as pf
    comp = _adapter()
    facts = await asyncio.to_thread(
        pf.remote_facts, t.dest, t.port, t.ssh_opts, pf.canary_paths())
    if not facts.get("ok"):
        raise HTTPException(502, facts)
    kind = "slurm" if facts.get("scheduler") == "slurm" else "ssh"
    try:
        known = {s.get("name") for s in await comp.sites_list()}
        name = inference.suggest_name(t.dest, known)
        user, _, host = t.dest.rpartition("@")
        cfg: dict = {"root": "~/.weft", "host": host or t.dest}
        if user:
            cfg["user"] = user
        if t.port:
            cfg["port"] = t.port
        opts = list(t.ssh_opts) + pf.trust_opts() + pf.identity_opts()
        cfg["ssh_opts"] = opts
        probed = await comp.register_site(name, kind, cfg, probe_only=True)
    except ComputeError as e:
        raise _http(e)
    caps = probed.get("capabilities") or {}
    proposal = inference.propose(
        caps, dest=t.dest, shared_paths=facts.get("present") or [],
        accounts=facts.get("accounts") or [], known_names=known)
    return {"capabilities": caps, "proposal": proposal,
            "facts": {k: facts.get(k) for k in ("present", "accounts",
                                                "scheduler")}}


@router.post("/api/compute/sites")
async def connect(req: ConnectRequest) -> dict:
    _require_self_service()
    """§5.5: register with weft (narrated via the event relay), mirror to
    weft-sites.yaml with the aba keys, kick background queue verification.
    Returns as soon as the site is registered — verification never blocks."""
    from core.compute import inference, preflight as pf, sites_config
    comp = _adapter()
    p = req.proposal
    opts = list(req.ssh_opts) + pf.trust_opts() + pf.identity_opts()
    cfg = inference.build_site_config(
        p.model_dump(), dest=req.dest, port=req.port,
        ssh_opts=opts if p.kind in ("ssh", "slurm") else None)
    try:
        result = await comp.register_site(p.name, p.kind, cfg)
    except ComputeError as e:
        raise _http(e)
    sites_config.upsert_site(p.name, p.kind, cfg, aba={
        "contract": p.contract, "use_for": list(p.use_for),
        "durable": p.durable,
        "storage": [e for e in p.long_term if e.get("path")]})
    _broadcast(p.name, "connected")
    selected = [r["name"] for r in p.partitions if r.get("selected")]
    if p.kind == "slurm" and selected:
        _start_verify(p.name, selected)
    return {"site": p.name, "capabilities": result.get("capabilities"),
            "verifying": bool(p.kind == "slurm" and selected)}


# ── background queue verification (§5.5) ─────────────────────────────────────

def _start_verify(name: str, partitions: Optional[list[str]] = None) -> bool:
    if (t := _verify_tasks.get(name)) and not t.done():
        return False
    _verify_state[name] = {"state": "running", "partitions": partitions}

    async def _run():
        from core.compute.adapter import get_compute
        try:
            r = await get_compute().site_probe_deep(name, partitions=partitions)
            parts = r.get("partitions") or {}
            failed = sorted(k for k, v in parts.items()
                            if isinstance(v, dict) and not v.get("ok", True))
            _verify_state[name] = {"state": "done", "ok": not failed,
                                   "partitions": parts, "failed": failed}
            _broadcast(name, "verified", ok=not failed,
                       note=(f"queue(s) failed verification: {', '.join(failed)}"
                             if failed else "test job ran on every queue"))
        except ComputeError as e:
            _verify_state[name] = {"state": "done", "ok": False,
                                   "error": e.to_payload()}
            _broadcast(name, "verified", ok=False, note=e.detail)
        finally:
            _verify_tasks.pop(name, None)

    _verify_tasks[name] = asyncio.get_running_loop().create_task(_run())
    return True


@router.post("/api/compute/sites/{name}/verify")
async def verify(name: str) -> dict:
    """Re-run the per-queue test jobs in the background (card upgrades via
    the `compute` notification when done)."""
    comp = _adapter()
    try:
        desc = await comp.sites_describe(name)
    except ComputeError as e:
        raise _http(e)
    allowed = ((desc.get("config") or {}).get("policy") or {}) \
        .get("partitions_allowed")
    started = _start_verify(name, allowed)
    return {"site": name, "started": started,
            "state": _verify_state.get(name, {})}


@router.post("/api/compute/sites/{name}/reprobe")
async def reprobe(name: str) -> dict:
    """Test connection: a fast login-node re-probe (§6)."""
    comp = _adapter()
    try:
        caps = await comp.site_probe(name)
    except ComputeError as e:
        raise _http(e)
    _broadcast(name, "reprobed")
    return {"site": name, "capabilities": caps}


# ── manage: edit / disconnect / free up ──────────────────────────────────────

@router.patch("/api/compute/sites/{name}")
async def edit_site(name: str, edit: SiteEdit) -> dict:
    _require_self_service()
    """Edit the aba-side keys (use_for, long-term storage, notes). The weft
    config is re-upserted so policy storage roles follow; name and
    connection details are fixed (disconnect + reconnect to change those)."""
    from core.compute import sites_config
    comp = _adapter()
    try:
        desc = await comp.sites_describe(name)
    except ComputeError as e:
        raise _http(e)
    kind, cfg = desc.get("kind"), dict(desc.get("config") or {})
    aba: dict = {}
    if edit.use_for is not None:
        aba["use_for"] = edit.use_for
    if edit.durable is not None:
        aba["durable"] = edit.durable
        if edit.durable:
            cfg["durable"] = True
        else:
            cfg.pop("durable", None)
    if edit.durable_path is not None:
        if edit.durable_path.strip():
            cfg["durable"] = edit.durable_path.strip()
        elif cfg.get("durable") is not True:
            cfg.pop("durable", None)
    if edit.long_term is not None:
        aba["storage"] = [e for e in edit.long_term if e.get("path")]
        stable = [e["path"] for e in aba["storage"] if e.get("stable")]
        policy = dict(cfg.get("policy") or {})
        storage = dict(policy.get("storage") or {})
        if stable:
            storage["large"] = stable[0]     # weft's role is single-valued
        else:
            storage.pop("large", None)
        policy["storage"] = storage
        cfg["policy"] = policy
    if edit.notes is not None:
        policy = dict(cfg.get("policy") or {})
        policy["notes"] = [n.strip() for n in edit.notes if n.strip()]
        cfg["policy"] = policy
    if edit.working_root is not None and edit.working_root.strip():
        cfg["root"] = edit.working_root.strip()
    try:
        await comp.register_site(name, kind, cfg)   # idempotent upsert
    except ComputeError as e:
        raise _http(e)
    entry = sites_config.upsert_site(name, kind, cfg, aba=aba or None)
    _broadcast(name, "edited")
    return {"site": name, "aba": entry.get("aba"), "config": cfg}


@router.delete("/api/compute/sites/{name}")
async def disconnect(name: str) -> dict:
    _require_self_service()
    """Forget the site (weft refuses while jobs/kernels/services are live —
    surfaced as 409 with the busy lists). Nothing on the machine is
    deleted; the YAML entry is dropped so it stays forgotten across boots."""
    from core.compute import sites_config
    if name == "local":
        raise HTTPException(400, "the local site cannot be disconnected")
    comp = _adapter()
    try:
        out = await comp.site_unregister(name)
    except ComputeError as e:
        raise _http(e)
    sites_config.remove_site(name)
    _verify_state.pop(name, None)
    _broadcast(name, "disconnected")
    return {"site": name, **(out if isinstance(out, dict) else {})}


@router.get("/api/compute/sites/{name}/holdings")
def holdings(name: str) -> dict:
    """§2 (more_weft_ui.md): what lives ONLY on this machine — kept results
    + dataset homes — so structural actions (Disconnect, durable-uncheck,
    Free up) can preview what they'd orphan BEFORE acting. Read-only, from
    recorded catalog state; never probes the site."""
    from core.data.ledger import site_holdings
    return site_holdings(name)


@router.get("/api/projects/{pid}/data-ledger")
def data_ledger_route(pid: str) -> dict:
    """§1: the project data-safety rollup — every valued item (dataset /
    run keeps) in exactly one state (safe / at_risk / changed / unknown),
    plus totals. The same query backs the Guide's data_safety_summary tool,
    so chat and UI cannot disagree."""
    from core.data.ledger import data_ledger
    return data_ledger(pid)


@router.get("/api/compute/orphans")
def orphans_plan() -> dict:
    """What DELETED projects are still holding (core.compute.reclaim.orphans).
    Dry. A project's directory and `weft_envs.json` survive its delete — the
    recovery archive — so a project deleted before the reclaim lane existed
    still names, on disk, the envs it held. Refuses (never sweeps) when the
    registry cannot be read."""
    from core.compute import reclaim
    return reclaim.orphans(confirm=False)


@router.post("/api/compute/orphans")
def orphans_sweep(req: GcRequest) -> dict:
    """Execute the orphan plan. Deliberately NOT automatic: evicting envs for
    projects deleted months ago must not be a side effect of a server boot.
    Never touches a project directory or its recovery archive."""
    from core.compute import reclaim
    out = reclaim.orphans(confirm=bool(req.confirm))
    if req.confirm:
        _broadcast("local", "gc", note="orphan projects reclaimed")
    return out


@router.post("/api/compute/sites/{name}/gc")
async def free_up(name: str, req: GcRequest) -> dict:
    _require_self_service()
    """'Free up space': plan (confirm=false) → sweep (confirm=true). The
    full per-env footprint surface stays in weft-ui; this is the one-number
    one-button scientist path (§6)."""
    comp = _adapter()
    try:
        if not req.confirm:
            return await comp.gc_plan(site=name)
        out = await comp.gc_sweep(name, confirm=True)
    except ComputeError as e:
        raise _http(e)
    _broadcast(name, "gc", note="space reclaimed")
    return out
