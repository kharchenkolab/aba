"""Compute-site tools: the Guide as a second front-end to Settings → Compute
(misc/compute_settings.md §9). "Connect my lab cluster, it's login.vbc.ac.at"
in chat drives the same access → probe → propose → confirm flow as the tab,
through the same core/compute pieces (preflight, inference, sites_config) —
the proposal the agent presents in chat IS the tab's proposal object.

Hard rules the tools enforce (not just document):
  * NEVER ask the user for a password, and never relay one — key setup hands
    the user an `ssh-copy-id` line to run in their OWN terminal (§5.2).
  * Host keys are trust-on-first-use with EXPLICIT user consent: show the
    fingerprint, and pass accept_hostkey=True only after the user confirms.
  * Connecting is a user decision: `connect_compute_site` refuses without
    `confirmed=True`, which you may set only after the user approved the
    proposal you presented.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_compute_sites_tools(mcp: FastMCP) -> None:
    """Register the compute-sites (Settings → Compute) tools on `mcp`."""

    @mcp.tool()
    def list_compute_sites() -> dict:
        """The machines aba can run analyses on (Settings → Compute): name,
        kind (local/ssh/slurm), health, capacity summary, and the aba-side
        keys — `use_for` (interactive/background/gpu placement hints) and
        `contract` (shared-fs: aba sees the user's files directly; detached:
        work must ship over). Read-only and fast (no ssh). Use it to answer
        "where can this run?" and to surface placement choices to the user
        — when a job runs remotely, say where and why."""
        from core.compute import sites_config
        from core.compute.adapter import get_compute
        comp = get_compute()
        rows = comp.sync_call("sites_list")
        for r in rows:
            r["aba"] = sites_config.aba_keys(r.get("name", "")) or (
                {"contract": "shared-fs",
                 "use_for": ["interactive", "background"]}
                if r.get("name") == "local" else {})
        return {"sites": rows}

    @mcp.tool()
    def probe_compute_site(dest: str, port: int | None = None,
                           accept_hostkey: bool = False) -> dict:
        """Probe a machine the user wants to connect (the ssh address they
        already use, e.g. 'me@login.cluster.edu') and return a complete
        setup PROPOSAL to present for their confirmation. Nothing is
        persisted by this call.

        Outcomes to handle:
        - `case: "hostkey"` — first contact. SHOW the user the fingerprint
          and ask them to confirm it is their machine; only then call again
          with accept_hostkey=True. Never accept silently.
        - `case: "auth"` — the machine wants a password. NEVER ask for the
          password. Relay `keysetup.command` (an ssh-copy-id line) for the
          user to run in THEIR OWN terminal, then probe again.
        - `case: "network"/"dns"` — relay `cause` (usually: connect the VPN).
        - `case: "ok"` — returns `proposal`: machine type, suggested name,
          working space, long-term store, queues (Slurm partitions), account,
          contract. Present it in plain language; every field is editable.
          After the user approves, call connect_compute_site(confirmed=True).
        """
        from core.compute import inference, preflight as pf
        from core.compute.adapter import get_compute, run_sync
        pre = pf.preflight(dest, port)
        case = pre.get("case")
        if case == "hostkey" and accept_hostkey and pre.get("hostkey"):
            pf.accept_hostkey(pre["hostkey"]["line"])
            pre = pf.preflight(dest, port)
            case = pre.get("case")
        if case == "auth":
            ks = pf.keysetup(dest, port)
            return {"case": "auth", "cause": pre.get("cause"),
                    "keysetup": ks,
                    "next": "have the USER run keysetup.command in their own "
                            "terminal (aba never sees the password), then "
                            "probe again"}
        if case != "ok":
            return {"case": case, "cause": pre.get("cause"),
                    "hostkey": pre.get("hostkey"),
                    "stderr": pre.get("stderr")}
        comp = get_compute()
        facts = pf.remote_facts(dest, port, None, pf.canary_paths())
        if not facts.get("ok"):
            return {"case": facts.get("case", "unknown"),
                    "cause": facts.get("detail")}
        kind = "slurm" if facts.get("scheduler") == "slurm" else "ssh"
        known = {s.get("name") for s in comp.sync_call("sites_list")}
        user, _, host = dest.rpartition("@")
        cfg = {"root": "~/.weft", "host": host or dest,
               "ssh_opts": pf.trust_opts() + pf.identity_opts()}
        if user:
            cfg["user"] = user
        if port:
            cfg["port"] = port
        probed = run_sync(comp.register_site(
            inference.suggest_name(dest, known), kind, cfg, probe_only=True))
        proposal = inference.propose(
            probed.get("capabilities") or {}, dest=dest,
            shared_paths=facts.get("present") or [],
            accounts=facts.get("accounts") or [], known_names=known)
        return {"case": "ok", "proposal": proposal,
                "next": "present the proposal to the user in plain language; "
                        "on approval call connect_compute_site with "
                        "confirmed=True (fields may be edited first)"}

    @mcp.tool()
    def connect_compute_site(dest: str, proposal: dict,
                             port: int | None = None,
                             confirmed: bool = False) -> dict:
        """Register a probed machine as a compute site (the user's approval
        is REQUIRED — call only with confirmed=True after they approved the
        proposal from probe_compute_site). Registers with weft, persists to
        weft-sites.yaml with the aba keys (contract, use_for, long-term
        storage). Queue verification (a small test job per queue) can then
        be run from Settings → Compute."""
        if not confirmed:
            return {"error": "user_confirmation_required",
                    "detail": "present the proposal and get the user's "
                              "explicit approval, then call again with "
                              "confirmed=True"}
        from core.compute import inference, preflight as pf, sites_config
        from core.compute.adapter import get_compute, run_sync
        comp = get_compute()
        opts = pf.trust_opts() + pf.identity_opts()
        cfg = inference.build_site_config(
            proposal, dest=dest, port=port,
            ssh_opts=opts if proposal.get("kind") in ("ssh", "slurm") else None)
        out = run_sync(comp.register_site(
            proposal["name"], proposal["kind"], cfg))
        sites_config.upsert_site(proposal["name"], proposal["kind"], cfg, aba={
            "contract": proposal.get("contract", "detached"),
            "use_for": list(proposal.get("use_for") or []),
            "storage": [e for e in (proposal.get("long_term") or [])
                        if e.get("path")]})
        return {"site": proposal["name"],
                "capabilities": out.get("capabilities"),
                "note": "connected; tell the user it appears under "
                        "Settings → Compute, where queues verify in the "
                        "background"}
