#!/usr/bin/env python
"""ONE definition of "what does this scenario REQUIRE, and can this host give it?"

A scenario may declare `requires: <name>` (today: `slurm`) because a local
background job would not exercise the path it guards. Three callers need that
answer and must never disagree:

  * `sweep.run_scenario` — PROVIDES the requirement to the runner subprocess.
    The runner decides from the resolved submitter
    (`core.jobs.submitter.submitter_name`, which reads `ABA_BATCH_SUBMITTER`),
    so "provide the requirement" is exactly "export the variable the platform
    already reads". The sweep never exported it, so on a box WITH working
    Slurm all three `requires: slurm` scenarios declined every time — they have
    never run, their baseline reference is itself an error, and the sweep still
    printed a green headline.
  * `sweep`'s PRE-FLIGHT — refuses the run when a selected scenario's
    requirement cannot be satisfied here. That is the arming: a sweep that
    silently drops the only scenarios covering the scheduler lane is reporting
    on 35 of 38 while claiming 38, and "measured nothing" must not read as
    success.
  * `runner.main` — the authoritative decline (exit 4), after boot.

Availability is DECLARATION + LIVENESS, not just "is sbatch installed":
`_slurm_lane()` degrades to the LOCAL weft lane when the deployment declares no
slurm-kind weft site, and the runner's precondition (submitter NAME == slurm)
cannot see that degrade — the scenario would run a local job while its row
claimed the scheduler was covered. That is the "fake more permissive than
reality" shape, so the declared site is part of the probe.

It deliberately does NOT ask the live substrate (`weft_slurm_site()` needs a
booted adapter and answers None in a bare pre-flight process — a check that
false-aborts a good sweep is a check that gets deleted).

    python regtest/harness/preconditions.py        # what the tree requires; exit 1 if unmet
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# `requires:` value → the environment a runner subprocess needs for that
# requirement to be SATISFIED. Keyed on the DECLARATION, never on a scenario
# id: the sweep must not carry a list of "the slurm ones".
REQUIREMENT_ENV: dict[str, dict[str, str]] = {
    "slurm": {"ABA_BATCH_SUBMITTER": "slurm"},
}

PING_TIMEOUT_S = 20


def requirement_of(spec: dict) -> str:
    """The scenario's declared requirement, normalized ('' when none).
    Same normalization the runner applies to `spec['requires']`."""
    return str(spec.get("requires") or "").strip().lower()


def requirement_env(req: str) -> dict:
    """Env overrides that SATISFY `req` ({} for no/unknown requirement)."""
    return dict(REQUIREMENT_ENV.get(req, {}))


def scenario_requirements(scen_dir, sids) -> dict:
    """{sid: requirement} for the selected scenarios that declare one."""
    import yaml
    out = {}
    for sid in sids:
        try:
            spec = yaml.safe_load((Path(scen_dir) / sid / "scenario.yaml").read_text()) or {}
        except Exception:  # noqa: BLE001 — discovery already tolerated this file
            continue
        req = requirement_of(spec)
        if req:
            out[sid] = req
    return out


# ---------- probes ----------

def sites_config_path_for(home) -> Path:
    """The deployment's site declarations for a GIVEN home.

    Mirrors `core.compute.adapter.sites_config_path()`, which derives it as
    `$ABA_HOME/weft-sites.yaml`. Taken as an argument rather than read from the
    environment because pre-flight must inspect the home the RUNNERS will use
    (see sweep.eval_home) — the same reason check_eval_home resolves it that
    way. tests/test_regtest_requirements.py pins the two in lockstep."""
    return Path(home) / "weft-sites.yaml"


def declared_slurm_sites(home) -> list[str]:
    """Names of `kind: slurm` weft sites declared for this home ([] when none).

    This is what decides whether `ABA_BATCH_SUBMITTER=slurm` actually routes to
    the cluster: with none declared, `core.jobs.submitter._slurm_lane` prints a
    note and hands the job to the LOCAL lane."""
    import os

    import yaml

    def _slurm_named(entries) -> list[str]:
        return [str(s.get("name")) for s in (entries or [])
                if isinstance(s, dict)
                and str(s.get("kind") or "").strip().lower() == "slurm"]

    found: list[str] = []
    # (1) the deployment's own site.yaml (`compute.sites`). A SHARED deployment
    #     declares its cluster here because weft-sites.yaml lives in $ABA_HOME
    #     and every user gets a fresh one with no installer step to write it.
    #     Reading only weft-sites.yaml is why slurm looked covered: the sweep
    #     ran against a personal install, which HAS the file, while the shipped
    #     OOD shape never did (found 2026-08-25, after every background job on
    #     that deployment had been quietly running in the session container).
    sc = (os.environ.get("ABA_SITE_CONFIG") or "").strip()
    if sc:
        try:
            sp = Path(sc).expanduser()
            if sp.is_file():
                doc = yaml.safe_load(sp.read_text()) or {}
                found += _slurm_named((doc.get("compute") or {}).get("sites"))
        except Exception:  # noqa: BLE001 — a broken file declares nothing
            pass
    # (2) the operator's per-home override
    p = sites_config_path_for(home)
    if p.is_file():
        try:
            doc = yaml.safe_load(p.read_text()) or {}
            found += _slurm_named(doc.get("sites"))
        except Exception:  # noqa: BLE001
            pass
    return list(dict.fromkeys(found))


def scontrol_ping() -> bool:
    """Is a slurm controller answering? (`scontrol ping`, rc 0.)"""
    try:
        return subprocess.run(["scontrol", "ping"], capture_output=True,
                              timeout=PING_TIMEOUT_S).returncode == 0
    except Exception:  # noqa: BLE001 — absent/hung binary is simply "no"
        return False


def slurm_problems(home, *, which=None, ping=None) -> list[str]:
    """Why `requires: slurm` cannot be honoured here. Empty == it can.

    `which`/`ping` are injectable so the guard can drive every shape of this
    host without one (tests/test_regtest_requirements.py)."""
    which = which or shutil.which
    ping = ping or scontrol_ping
    problems: list[str] = []
    missing = [b for b in ("sbatch", "scontrol") if not which(b)]
    if missing:
        problems.append(
            f"no Slurm client on PATH ({', '.join(missing)} absent) — the "
            f"scheduler scenarios cannot run on this host")
    elif not ping():
        problems.append(
            "`scontrol ping` does not answer — a Slurm client is installed but "
            "no controller is reachable from this node")
    if not declared_slurm_sites(home):
        problems.append(
            f"no `kind: slurm` weft site declared in {sites_config_path_for(home)} "
            f"— ABA_BATCH_SUBMITTER=slurm would DEGRADE to the local weft lane "
            f"(core/jobs/submitter.py::_slurm_lane), so the scenario would run a "
            f"local job while its row claimed the scheduler was covered")
    return problems


REQUIREMENT_PROBES = {"slurm": slurm_problems}


def unmet_requirements(reqs: dict, home, **probe_kw) -> dict:
    """{requirement: [problems]} for the DISTINCT requirements in `reqs`.

    An UNKNOWN requirement is a problem in itself, not a no-op: the runner only
    knows `slurm`, so a typo'd or newly-invented `requires:` value is honoured
    by nobody and the scenario runs as if it had declared nothing."""
    out: dict[str, list[str]] = {}
    for req in sorted(set(reqs.values())):
        if not req:
            continue
        probe = REQUIREMENT_PROBES.get(req)
        if probe is None:
            out[req] = [f"unknown requirement `requires: {req}` — no probe and no "
                        f"environment provide it, so it would be silently ignored "
                        f"(add it to REQUIREMENT_ENV/REQUIREMENT_PROBES or fix the "
                        f"scenario's declaration)"]
            continue
        problems = probe(home, **probe_kw)
        if problems:
            out[req] = problems
    return out


def check_requirements(scen_dir, sids, home, **probe_kw) -> dict:
    """Pre-flight verdict for a selection: what it requires, and what is unmet.

    `examined` is the ARMING datum — zero scenarios declaring a requirement
    makes this check vacuous, and a clean bill from a check that inspected
    nothing is the failure this convention exists to stop."""
    reqs = scenario_requirements(scen_dir, sids)
    return {"requiring": reqs,
            "examined": len(reqs),
            "problems": unmet_requirements(reqs, home, **probe_kw)}


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    from core import config as _c
    home = _c.aba_home()
    root = Path(__file__).resolve().parents[1] / "scenarios"
    sids = sorted(p.parent.name for p in root.glob("*/scenario.yaml"))
    res = check_requirements(root, sids, home)
    print(f"[preconditions] home={home}  scenarios={len(sids)}  "
          f"declaring a requirement={res['examined']}")
    for sid, req in sorted(res["requiring"].items()):
        print(f"    {sid}: requires {req}")
    for req, problems in sorted(res["problems"].items()):
        for p in problems:
            print(f"[preconditions] UNMET {req}: {p}")
    return 1 if res["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
