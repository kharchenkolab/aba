"""Transport/mechanism-truth oracle — "did execution actually go through the
substrate?"

Why this exists: outcome oracles cannot see mechanism. During the substrate
migration, the legacy local kernel lane and the substrate lane executed the
same environment and produced identical results, records, and surfaces — so
every outcome-level test stayed green while the platform silently ran the
legacy lane by default for months. The mechanism truth was CAPTURED all along
(each exec record is stamped with a `compute` block naming its substrate) but
no oracle ever read it. This module closes that gap: it walks a project's
runs' execution records over the mechanism-truth surface
(`GET /api/runs/{id}/execs`) and flags every record that self-identifies as a
legacy execution.

Predicate (v1, migration-flagging): a record whose `compute.substrate` is
present and NOT the substrate is a legacy execution → fail. Records with no
`compute` block are not flagged here (older records and doctrine-exempt
direct-exec lanes carry none); the post-cutover invariant test tightens the
absence rule for interactive kinds.

Driver-agnostic like the surface oracle: `client` needs only .get(url).
Never raises; returns "transport:<class>:<detail>" failure strings, plus the
count of records checked via `transport_truth(client, pid)["checked"]` so a
caller can refuse a vacuous pass (zero records examined proves nothing).
"""
from __future__ import annotations

SUBSTRATE = "weft"


def _get(client, url):
    try:
        return client.get(url)
    except Exception as e:  # noqa: BLE001 — a crashed route is a finding
        class _R:
            status_code = 599
            text = f"{type(e).__name__}: {e}"

            @staticmethod
            def json():
                return {}
        return _R()


def transport_truth(client, pid: str, *, run_ids=None,
                    max_runs: int = 20) -> dict:
    """{failures: [...], checked: n, runs: n} — see module docstring."""
    fails: list[str] = []
    checked = 0
    if run_ids is None:
        r = _get(client, f"/api/entities?project_id={pid}&include_archived=false")
        ents = r.json() if r.status_code == 200 else []
        ents = ents if isinstance(ents, list) else ents.get("entities", [])
        run_ids = [e["id"] for e in ents if e.get("type") == "analysis"
                   and e.get("status") == "active"][:max_runs]
    for rid in run_ids:
        r = _get(client, f"/api/runs/{rid}/execs")
        if r.status_code == 404:
            continue    # surface absent (older server) or run gone — zero
                        # checked; callers treat a vacuous pass as unproven
        if r.status_code != 200:
            fails.append(f"transport:execs_unreadable:{rid} -> {r.status_code}")
            continue
        for rec in (r.json() or {}).get("execs") or []:
            comp = rec.get("compute")
            if not isinstance(comp, dict):
                continue                      # absent block: not adjudicated in v1
            checked += 1
            sub = comp.get("substrate")
            if sub != SUBSTRATE:
                fails.append(
                    f"transport:legacy_exec:{rid}/{rec.get('exec_id')} "
                    f"substrate={sub!r} kind={rec.get('kind')} "
                    f"lang={rec.get('language')}")
    return {"failures": fails, "checked": checked, "runs": len(run_ids)}


def transport_truth_failures(client, pid: str, **kw) -> list[str]:
    """The failure list alone (the runner's check-shaped entry point)."""
    return transport_truth(client, pid, **kw)["failures"]
