"""Install regression tests: can a user actually GET the library they ask for?

The suite could already tell whether a turn achieved something. It could not
tell what the turn SPENT to achieve it, and it never asked the one question a
user asks constantly — "I need library X" — against a real deployment.

Live, 2026-08-25: a request for an R library that the mounted base pack already
contains and verifies built a 2.0 GB duplicate environment over ~15 minutes.
Every existing assertion was satisfiable by that outcome. This probe exists so
that class of failure is measured rather than reasoned about:

  * it drives the REAL agent surface (one turn per package, same lane as
    live_surface_probe) against a running server — a staged image, ideally;
  * it judges RECORDED state (exec records + the project's env registry),
    never the agent's account of what it did;
  * it records COST — named envs created, wall seconds — so "worked" and
    "worked ruinously" are different verdicts;
  * for anything a base pack PROVES it provides (`spec.verify`), creating an
    env at all is a FAILURE, not a slow success.

The package matrix is data (regtest/data/install_matrix.json), so widening
coverage is an edit to a list, not to this file.

    python live_install_probe.py --base http://127.0.0.1:8000 \
        --projects-dir /tmp/aba-verify-XXXX/.aba/runtime/projects \
        [--ecosystem bioconductor] [--limit 20] [--only Signac,DESeq2] \
        [--results out.json] [--pack-provided-only]

Exit 1 if any entry FAILED (unusable, or built an env it had no business
building). Slow-but-correct is reported, not fatal, unless --strict-seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# Comma-separated: field-usage rankings first, our own recipes last (they win).
MATRIX = ",".join([
    str(ROOT / "regtest" / "data" / "install_matrix.json"),
    str(ROOT / "regtest" / "data" / "install_matrix_recipes.json"),
])

# A capability the base pack proves it can load must cost NOTHING to "install".
PACK_ENV_CEILING = 0
# Anything else may build at most one isolated env for itself.
INSTALL_ENV_CEILING = 1


def _load_matrix(paths) -> list[dict]:
    """Merge one or more matrix files by NAME (later files enrich earlier ones).

    Two sources feed this, and they answer different questions: what the field
    uses most (download rankings) and what OUR OWN recipes assume is installed.
    The second is the sharper one — a tool a recipe reaches for and cannot find
    is a promise the platform already broke — so it is merged last and wins on
    conflicting fields."""
    if isinstance(paths, (str, Path)):
        paths = [x for x in str(paths).split(",") if x.strip()]
    merged: dict[str, dict] = {}
    seen_any = False
    for one in paths:
        f = Path(one)
        if not f.exists():
            continue
        seen_any = True
        doc = json.loads(f.read_text())
        entries = doc.get("entries") if isinstance(doc, dict) else doc
        for e in entries or []:
            if not isinstance(e, dict) or not e.get("name"):
                continue
            cur = merged.setdefault(e["name"], {})
            cur.update({k: v for k, v in e.items() if v is not None})
            cur.setdefault("name", e["name"])
    if not seen_any:
        # A normal exception, not SystemExit: the pack-provided gate CATCHES
        # this and proceeds from the packs alone. SystemExit is a
        # BaseException, so it slipped past that guard and let a missing data
        # file disable the regression gate — the exact "a gate that cannot run
        # is not a gate" shape this whole probe exists to prevent.
        raise FileNotFoundError(f"no matrix file found among: {paths}")
    return list(merged.values())


def pack_provided() -> dict[str, str]:
    """``{name: language}`` for every name the SHIPPED base packs prove they load.

    Read from the repo's own pack yamls, never from the running server: the
    probe must state an INDEPENDENT expectation, not ask the system under test
    what it believes about itself. `spec.verify` is the right source because a
    pack that cannot load what it lists there fails to publish — so these names
    are provided in fact, and a request for one must cost nothing."""
    import yaml
    out: dict[str, str] = {}
    for yf in sorted((ROOT / "install" / "core" / "envs").glob("*.yaml")):
        try:
            doc = yaml.safe_load(yf.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        for pack in (doc.get("packs") if isinstance(doc.get("packs"), list) else [doc]):
            if not isinstance(pack, dict):
                continue
            langs = [str(x).lower() for x in (pack.get("languages") or [])]
            lang = "r" if "r" in langs else "python"
            verify = ((pack.get("spec") or {}).get("verify") or {})
            for key in ("loads", "import", "imports"):
                for n in (verify.get(key) or []):
                    out.setdefault(str(n), lang)
    return out


def _consume(stream, cap: dict) -> None:
    for line in stream.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        try:
            ev = json.loads(line[5:].strip())
        except Exception:  # noqa: BLE001
            continue
        t = ev.get("type")
        # Count every event type seen. An instrument that parses the wrong
        # event names measures nothing and reports it as "the agent did
        # nothing" — which is indistinguishable from a finding. `kinds` is
        # what lets the probe notice it has gone blind (see _instrument_fault).
        cap["kinds"][t] = cap["kinds"].get(t, 0) + 1
        # run_id rides on ANY event, not a dedicated one. Keying it to a
        # `run_started` type meant run_id stayed None, so the approval-gate
        # resume loop below never ran and every turn that paused for approval
        # was silently abandoned half-done.
        if ev.get("run_id"):
            cap["run_id"] = ev["run_id"]
        if t in ("tool_start", "tool_call"):
            cap["tools"].append(ev.get("name") or ev.get("tool") or "?")
        elif t == "tool_result":
            r = ev.get("result") or {}
            if isinstance(r, dict) and r.get("job_id"):
                cap.setdefault("jobs", []).append(r["job_id"])
            # The PLATFORM's own verdict on the request is recorded state, not
            # the agent's account of it — keep it, so "it is ready" can be
            # checked against what ensure_capability actually returned.
            if isinstance(r, dict) and (r.get("status") or "loads" in r):
                cap.setdefault("cap_results", []).append(
                    {"tool": ev.get("name"), "status": r.get("status"),
                     "loads": r.get("loads"),
                     "version": r.get("version"), "library": r.get("library"),
                     "import_name": r.get("import_name"),
                     "packs": r.get("packs")})
        elif t in ("error", "cancelled"):
            cap["errors"].append(str(ev)[:300])
        elif t == "text":
            cap["text"].append(str(ev.get("text") or ""))


def _env_count(projects_dir: Path | None, pid: str) -> "tuple[int, int] | None":
    """``(named_envs, session_additions)`` for a project, or None if unreadable.

    TWO numbers, because there are two ways to spend on a request and only one
    of them mints a named env. A session install adds packages to the project's
    default weft session and creates NO named env — so counting named envs
    alone reported "cost nothing" for a request that had just installed and
    solved a package. That understates cost in exactly the direction that
    flatters us, and would have let the original incident hide again had it
    taken the session lane instead of the isolated-env lane.

    None is not zero: an unmeasured ceiling must fail, never pass."""
    if projects_dir is None:
        return None
    p = Path(projects_dir) / str(pid) / "weft_envs.json"
    if not p.exists():
        return (0, 0)
    try:
        doc = json.loads(p.read_text()) or {}
        named = len(doc.get("envs") or {})
        adds = sum(len((row or {}).get("additions") or [])
                   for row in (doc.get("default") or {}).values())
        return (named, adds)
    except Exception:  # noqa: BLE001
        return None


def _prompt_for(entry: dict) -> str:
    """State the OUTCOME, never the mechanism.

    Deliberately does NOT say install / env / pack / conda: naming a mechanism
    is how a scenario ends up testing obedience instead of behaviour. The user
    wants to use the thing and see it work."""
    lang = (entry.get("language") or "").lower()
    name = entry["name"]
    if lang == "r":
        return (f"I need to use the R library {name} in this project. "
                f"Please make sure it is available and show me its version.")
    if lang == "python":
        return (f"I need to use the Python package {name} in this project. "
                f"Please make sure it is available and show me its version.")
    return (f"I need to run the command-line tool {name} in this project. "
            f"Please make sure it is available and show me its version.")


def _await_job(c, jid: str, timeout_s: float) -> dict:
    """Poll one background job to a terminal state and report WHERE it ran.

    "Can I install it" and "can I use it on the cluster" are different
    questions with different failure modes — a library present on the login
    node and absent on a compute node is a normal, invisible way for this to
    break. The site comes from the submitter's own record, so a job that
    quietly ran on the local lane is not counted as cluster coverage."""
    deadline = time.time() + timeout_s
    row: dict = {}
    while time.time() < deadline:
        try:
            row = c.get(f"/api/jobs/{jid}").json() or {}
        except Exception:  # noqa: BLE001
            row = {}
        if (row.get("status") or "") in ("done", "failed", "cancelled"):
            break
        time.sleep(5)
    params = row.get("params") or {}
    return {"job_id": jid, "status": row.get("status"),
            "site": params.get("weft_site"),
            "log_tail": (row.get("log_tail") or "")[-300:]}


def _drive(c, pid: str, tid: str, text: str, timeout: float) -> dict:
    """One agent turn; returns the capture. Approval gates resolved like the UI."""
    cap: dict = {"run_id": None, "tools": [], "errors": [], "text": [],
                 "jobs": [], "cap_results": [], "kinds": {}}
    with c.stream("POST", "/api/chat", timeout=timeout,
                  json={"text": text, "project_id": pid, "thread_id": tid}) as r:
        r.raise_for_status()
        _consume(r, cap)
    for _ in range(6):
        rid = cap["run_id"]
        if not rid:
            break
        if c.get(f"/api/turns/{rid}").json().get("state") != "awaiting_user":
            break
        with c.stream("POST", f"/api/turns/{rid}/resume", timeout=timeout,
                      json={"user_text": "Yes, go ahead."}) as r2:
            r2.raise_for_status()
            _consume(r2, cap)
    return cap


def running_build(c) -> dict:
    """``{release, built_from}`` of the server under test, or {}.

    Every row records it. A sweep runs for hours and a release can be promoted
    underneath it, at which point a single results file silently spans two
    builds with nothing to say which row came from which — the same provenance
    gap that let a substrate change reach users unnoticed, one layer down. A
    result that cannot be attributed to a build is not evidence about a build."""
    try:
        h = c.get("/api/health").json() or {}
        return {k: h[k] for k in ("release", "built_from") if k in h}
    except Exception:  # noqa: BLE001 — an older server just says nothing
        return {}


def _instrument_fault(cap: dict, executed: bool) -> str | None:
    """Is this probe BLIND rather than the deployment broken?

    Written after the probe read the wrong SSE event names, recorded zero tool
    calls for all 33 packages, and produced a "the agent never checked
    anything" finding that was purely an artifact of its own parser. A measured
    zero and an unmeasured zero look identical in a results table, so the
    instrument has to be able to tell them apart and say so.

    Two tells: no events at all (nothing was parsed), or a turn that demonstrably
    RAN something while the parser saw no tool events (the names are wrong)."""
    if not cap.get("kinds"):
        return "no SSE events parsed at all — wrong stream format or dead turn"
    if cap.get("errors"):
        # The turn FAILED. The parser is fine — there were no tool events
        # because nothing ran. Blaming the instrument here is a misdiagnosis
        # that costs a whole run: 42 packages once reported "the probe is
        # reading the wrong event names" when every turn had errored in 0.3s
        # and the real cause was never printed. A failed turn is a finding
        # about the DEPLOYMENT, and it must carry its reason.
        return None
    if executed and not cap.get("tools"):
        return (f"a turn produced exec records but the parser saw NO tool events; "
                f"event types present: {sorted(cap['kinds'])} — the probe is reading "
                f"the wrong event names and its tool/job counts are meaningless")
    return None


def _turn_failed(cap: dict) -> str | None:
    """The turn errored — say what the error WAS.

    `turn_errors` was a count. A count cannot be acted on: 42 identical
    failures with no reason is the same amount of information as one."""
    errs = cap.get("errors") or []
    if not errs:
        return None
    first = str(errs[0])[:300]
    more = f" (+{len(errs) - 1} more)" if len(errs) > 1 else ""
    return f"the turn errored and no tool ran: {first}{more}"


def _exec_ok(c, pid: str, run_ids: list[str]) -> tuple[bool, str]:
    """Did SOMETHING actually execute successfully for this request?

    Judged on exec records, not on the reply — an agent that says "Signac is
    ready" while nothing ran must not score as ready."""
    seen = 0
    for rid in run_ids:
        try:
            r = c.get(f"/api/runs/{rid}/execs")
        except Exception:  # noqa: BLE001
            continue
        if r.status_code != 200:
            continue
        for rec in (r.json() or {}).get("execs") or []:
            seen += 1
            rc = rec.get("returncode")
            if not rec.get("error") and rc in (None, 0):
                return True, f"{seen} exec record(s), at least one clean"
    return False, f"{seen} exec record(s), none clean"


def probe_one(c, entry: dict, *, timeout: float, projects_dir: Path | None,
              pack_names, background: bool = False,
              job_timeout: float = 900.0, build: dict | None = None,
              project: str | None = None) -> dict:
    name = entry["name"]
    entry = {**entry, **(build or {})}          # every row says which build produced it
    slug = "".join(ch if ch.isalnum() else "-" for ch in name).lower()[:40]
    # One bad entry must not end a 100+ package sweep: record and move on.
    try:
        if project:
            # Run INSIDE an existing project. A fresh project per package is the
            # easiest world the product has: no named envs, no recorded session
            # additions, no base that has moved, nothing a previous request
            # left behind. Seeding a lived-in home while still minting a virgin
            # project per package tested none of it — the fixture was
            # decoration, which is precisely what its own armed guard exists to
            # prevent, committed one layer up.
            pid = project
            r = c.post(f"/api/projects/{pid}/open")
            if r.status_code >= 400:
                # Never fall back to creating one. A silent fallback here turns
                # "we tested accumulated state" into "we tested nothing" and
                # reports it as a pass.
                return {**entry, "verdict": "error",
                        "detail": f"could not open project {pid!r} "
                                  f"(HTTP {r.status_code}) — refusing to fall "
                                  f"back to a fresh project, which would test "
                                  f"the opposite of what was asked"}
        else:
            pid = c.post("/api/projects",
                         json={"name": f"install-{slug}"}).json().get("id")
            c.post(f"/api/projects/{pid}/open")
        tid = c.post("/api/threads",
                     json={"project_id": pid, "title": f"install-{slug}"}).json().get("id")
    except Exception as e:  # noqa: BLE001
        return {**entry, "verdict": "error",
                "detail": f"setup: {type(e).__name__}: {e}"[:300]}
    if not pid or not tid:
        return {**entry, "verdict": "error", "detail": "project/thread creation failed"}

    envs_before = _env_count(projects_dir, pid)
    t0 = time.time()
    try:
        cap = _drive(c, pid, tid, _prompt_for(entry), timeout)
    except Exception as e:  # noqa: BLE001 — a wedged turn is a finding, not a crash
        return {**entry, "verdict": "error", "seconds": round(time.time() - t0, 1),
                "detail": f"{type(e).__name__}: {e}"[:300]}
    seconds = round(time.time() - t0, 1)
    envs_after = _env_count(projects_dir, pid)
    if envs_before is None or envs_after is None:
        made = adds = None
    else:
        made = envs_after[0] - envs_before[0]
        adds = envs_after[1] - envs_before[1]

    # A turn can legitimately END by submitting a background job — the work is
    # real, it is just not finished when the stream closes. Judging exec records
    # at that moment reports "nothing ran" for a request that ran plenty.
    # Found in the sweep's own first results: an entry that spent 16 minutes,
    # built an env and submitted a job scored `unavailable`.
    submitted = list(cap.get("jobs") or [])
    if submitted:
        row_jobs = [_await_job(c, j, job_timeout) for j in submitted]
    else:
        row_jobs = []
    try:
        ents = c.get("/api/entities",
                     params={"project_id": pid, "include_archived": True}).json()
        ents = ents if isinstance(ents, list) else ents.get("entities", [])
        runs = [e["id"] for e in ents if e.get("type") == "analysis"]
        ok, exec_detail = _exec_ok(c, pid, runs)
    except Exception as e:  # noqa: BLE001
        return {**entry, "verdict": "error", "seconds": seconds,
                "detail": f"state read: {type(e).__name__}: {e}"[:300]}
    if not ok and row_jobs:
        # the job IS the proof for an async turn
        done = [j for j in row_jobs if j.get("status") == "done"]
        if done:
            ok = True
            exec_detail = (f"no synchronous exec, but {len(done)}/{len(row_jobs)} "
                           f"submitted job(s) completed")

    row = {**entry, "project_id": pid, "seconds": seconds,
           "envs_created": made, "session_adds": adds,
           "tools": len(cap["tools"]),
           # WHICH tools, not just how many. Both of the sweep's first two
           # failures had healthy tool activity and no exec record, and a bare
           # count could not say whether the deployment failed or the probe
           # judged too early.
           "tool_names": sorted(set(cap["tools"]))[:12],
           "turn_errors": len(cap["errors"]),
           # the REASON, not just the count — see _turn_failed
           **({"turn_error_detail": str(cap["errors"][0])[:300]}
              if cap.get("errors") else {}),
           "exec": exec_detail,
           "submitted_jobs": row_jobs,
           "event_kinds": sorted(cap.get("kinds") or {})}
    _fault = _instrument_fault(cap, ok)
    if _fault:
        row["verdict"] = "instrument_fault"
        row["detail"] = _fault
        return row
    _failed = _turn_failed(cap)
    if _failed and not cap.get("tools"):
        # errored before any tool ran — a deployment finding, with its cause
        row["verdict"] = "turn_failed"
        row["detail"] = _failed
        return row

    # SECOND question: does it work OFF the login node? A library present where
    # the controller runs and absent on a compute node is a normal way for this
    # to break, and it is invisible to a probe that only ever runs in-session.
    if background and ok:
        try:
            bcap = _drive(c, pid, tid,
                          f"Now run that as a background job on the cluster: load "
                          f"{name} there and print its version.", timeout)
            jobs = [_await_job(c, j, job_timeout) for j in (bcap.get("jobs") or [])]
        except Exception as e:  # noqa: BLE001
            jobs = []
            row["background_error"] = f"{type(e).__name__}: {e}"[:200]
        row["jobs"] = jobs
        if not jobs:
            row["background"] = "not_submitted"
        elif not any((j.get("status") == "done") for j in jobs):
            row["background"] = "failed"
            row["verdict"] = "background_failed"
            row["detail"] = (f"usable in-session but the offloaded job did not "
                             f"complete: {jobs}")
            return row
        elif all(str(j.get("site") or "local").lower() == "local" for j in jobs):
            # Not a failure of the LIBRARY, but the run proves nothing about the
            # cluster — say so rather than banking it as cluster coverage.
            row["background"] = "ran_locally"
        else:
            row["background"] = "on_cluster"
    provided = name in pack_names
    row["pack_provided"] = provided
    ceiling = PACK_ENV_CEILING if provided else INSTALL_ENV_CEILING

    # TWO kinds of recorded proof, and neither is the agent's prose:
    #   * an exec record — something actually ran and exited clean;
    #   * the platform's own ensure_capability verdict for THIS name.
    # Requiring an exec was too strict: recognizing a pack-provided library is
    # correctly answered without running anything, and marking that
    # "unavailable" would have punished exactly the behaviour this probe was
    # built to reward. Requiring only the verdict would be too loose — it is a
    # claim about an env, and DESeq2 taught us a claim about an env can be
    # false. So: accept either, and RECORD which, so a run that was only ever
    # asserted is visible as such instead of banked as proof.
    # `inspect_env(name=...)` runs a REAL requireNamespace/import in the actual
    # runtime and reports {loads, version}. That is not a claim about an env, it
    # is a measurement of one — the same question this probe asks, answered by
    # the platform's own probe. Excluding it scored `reticulate` as
    # `unavailable` on a deployment where it loads at 1.46.0: the agent looked,
    # found it present, and correctly did nothing, and the probe called that a
    # failure to provide it.
    #
    # `status: "ok"` is about the PROBE having run, not about the package —
    # `{status: ok, loads: false}` is an honest "I checked, it is not there" and
    # must never count as proof. Key on `loads`, not on status.
    verdicts = [r for r in (cap.get("cap_results") or [])
                if str(r.get("status") or "") in
                ("ready", "provided_by_pack", "already_available")
                or r.get("loads") is True]
    row["platform_verdict"] = verdicts[0] if verdicts else None
    # PROOF MUST BE ABOUT THIS PACKAGE. An exec record only says that SOMETHING
    # ran clean in the project — the agent explaining, in working R, that the
    # library is unavailable produces one. Measured 2026-08-26: the sweep scored
    # BPCells and SeuratWrappers `ready_from_pack` with `proof: executed`, and
    # neither is in the pack; a direct requireNamespace against the published
    # image says `absent` for both. Every "ready" count was inflated by however
    # many of those there were.
    #
    # A package-specific signal is the platform's own verdict for THIS name
    # (ensure_capability ready/provided_by_pack, or inspect_env loads=True).
    # An exec alone now yields `unverified`: activity happened, availability was
    # not established, and the two must not share a verdict.
    row["proof"] = ("verified" if verdicts else "unverified" if ok else "none")
    if not ok and not verdicts:
        row["verdict"] = "unavailable"
        row["detail"] = ("neither an exec record nor a platform readiness verdict "
                         "for this request — " + exec_detail)
        return row
    if not verdicts:
        # Something ran, but nothing said THIS package is available. Reporting
        # that as success is how two packages the pack does not contain were
        # counted as provided by it.
        #
        # ONE exception, and it is gated on a fact the agent cannot influence:
        # a package the SHIPPED PACKS provide, which cost no env and no session
        # addition, and whose turn ran clean. For a core library the agent
        # reasonably skips ensure_capability and just imports it — numpy and
        # pandas did exactly that, in 10 and 7 seconds, and failing a release
        # for it is a false alarm. False alarms are how gates get ignored.
        #
        # `pack_provided` is computed from the packs on disk, not from anything
        # the turn said, so an ABSENT package can never take this path — the
        # BPCells/SeuratWrappers loophole stays shut. Still not "verified": the
        # proof is circumstantial, and it is reported as its own class.
        if row.get("pack_provided") and made == 0 and adds == 0:
            row["verdict"] = "assumed_from_pack"
            row["proof"] = "assumed"
            row["detail"] = ("no platform verdict — the turn used it directly "
                             "and it worked, at zero cost; the pack does ship "
                             "it — " + exec_detail)
            return row
        row["verdict"] = "unverified"
        row["detail"] = ("work happened but no platform verdict names this "
                         "package as available — " + exec_detail)
        return row
    if made is None:
        row["verdict"] = "unmeasured"
        row["detail"] = ("env count could not be read (projects-dir wrong?) — "
                         "an unmeasured ceiling is not a pass")
        return row
    if made > ceiling:
        row["verdict"] = "wasteful"
        row["detail"] = (
            f"usable, but built {made} env(s) against a ceiling of {ceiling}"
            + (" — this library is PROVEN to load in a shipped base pack, so "
               "the correct answer costs nothing" if provided else ""))
        return row
    if made > 0:
        row["verdict"] = "installed"           # an isolated env was built for it
    elif adds:
        row["verdict"] = "installed_session"   # solved into the project session
    else:
        row["verdict"] = "ready_from_pack"     # nothing was spent
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--matrix", default=MATRIX,
                    help="one or more JSON matrix files, comma-separated")
    ap.add_argument("--exclude-ecosystem", default="stdlib",
                    help="comma-separated ecosystems to skip (default: stdlib — "
                         "a language's own standard library is not an install test)")
    ap.add_argument("--projects-dir", default=None,
                    help="PROJECTS_DIR of the server under test (for env counting)")
    ap.add_argument("--ecosystem", default=None)
    ap.add_argument("--language", default=None)
    ap.add_argument("--only", default=None, help="comma-separated names")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--top", type=int, default=0,
                    help="highest-priority N: most-used by OUR recipes first, "
                         "then field download rank")
    ap.add_argument("--skip-pack-provided", action="store_true",
                    help="exclude names a base pack already provides (they have "
                         "their own scope, --pack-provided-only)")
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--results", default=None,
                    help="JSON file; existing entries are SKIPPED (resumable)")
    ap.add_argument("--project", default=None,
                    help="run inside an EXISTING project (accumulated state) "
                         "instead of minting a fresh one per package")
    ap.add_argument("--pack-provided-only", action="store_true",
                    help="only the names a shipped base pack proves it loads")
    ap.add_argument("--background", action="store_true",
                    help="also ask for a background/cluster run of each library and "
                         "judge WHERE it landed")
    ap.add_argument("--job-timeout", type=float, default=900.0)
    ap.add_argument("--strict-seconds", type=float, default=0.0,
                    help="also fail any entry slower than this")
    a = ap.parse_args()

    import httpx   # deferred so --help works without it

    pack_names = pack_provided()
    # The pack-provided gate must be SELF-SUFFICIENT: it is the regression gate
    # for a live incident, so it cannot be disabled by a missing or trimmed
    # matrix file. The packs themselves are the source; the matrix only enriches.
    if a.pack_provided_only:
        try:
            known = {e["name"]: e for e in _load_matrix(a.matrix)}
        except Exception:  # noqa: BLE001 — no matrix yet is fine for this scope
            known = {}
        entries = [known.get(n, {"name": n, "language": lang,
                                 "ecosystem": "base-pack", "package": None})
                   for n, lang in sorted(pack_names.items())]
    else:
        try:
            entries = _load_matrix(a.matrix)
        except FileNotFoundError as e:
            raise SystemExit(str(e)) from e
    if a.ecosystem:
        entries = [e for e in entries if e.get("ecosystem") == a.ecosystem]
    if a.exclude_ecosystem:
        _skip = {x.strip() for x in a.exclude_ecosystem.split(",") if x.strip()}
        entries = [e for e in entries if e.get("ecosystem") not in _skip]
    if a.language:
        entries = [e for e in entries if e.get("language") == a.language]
    if a.only:
        want = {x.strip() for x in a.only.split(",") if x.strip()}
        entries = [e for e in entries if e["name"] in want]

    done: dict[str, dict] = {}
    resfile = Path(a.results) if a.results else None
    if resfile and resfile.exists():
        try:
            done = {r["name"]: r for r in json.loads(resfile.read_text())
                    if isinstance(r, dict) and r.get("name")}
        except Exception:  # noqa: BLE001
            done = {}
    entries = [e for e in entries if e["name"] not in done]
    if a.top:
        # Priority = what OUR OWN recipes reach for most, then field usage.
        # A tool a recipe assumes and cannot get is a promise already broken,
        # so recipe frequency outranks download popularity.
        entries.sort(key=lambda e: (-(e.get("n_recipes") or 0),
                                    e.get("popularity_rank") or 10_000,
                                    e.get("name", "")))
        entries = entries[:a.top]
    if a.skip_pack_provided:
        entries = [e for e in entries if e["name"] not in pack_names]
    if a.limit:
        entries = entries[:a.limit]

    print(f"[install-probe] {len(entries)} to run "
          f"({len(done)} already recorded), pack-provided names known: "
          f"{len(pack_names)}", flush=True)

    rows = list(done.values())
    with httpx.Client(base_url=a.base, timeout=a.timeout) as c:
        build = running_build(c)
        if build:
            print(f"[install-probe] server build: {build}", flush=True)
        if a.project:
            print(f"[install-probe] running inside EXISTING project "
                  f"{a.project} (accumulated state)", flush=True)
        for i, e in enumerate(entries, 1):
            print(f"[install-probe] {i}/{len(entries)} {e['name']} "
                  f"({e.get('ecosystem')})…", flush=True)
            row = probe_one(c, e, timeout=a.timeout,
                            projects_dir=Path(a.projects_dir) if a.projects_dir else None,
                            pack_names=pack_names, background=a.background,
                            job_timeout=a.job_timeout, build=build,
                            project=a.project)
            print(f"    -> {row['verdict']}  {row.get('seconds')}s  "
                  f"envs={row.get('envs_created')}", flush=True)
            rows.append(row)
            if resfile:
                resfile.parent.mkdir(parents=True, exist_ok=True)
                resfile.write_text(json.dumps(rows, indent=1))

    by = {}
    for r in rows:
        by.setdefault(r.get("verdict", "?"), []).append(r["name"])
    print("\n== install probe summary ==")
    for v in ("ready_from_pack", "assumed_from_pack", "installed_session",
              "installed", "wasteful", "unverified", "unavailable",
              "unmeasured", "background_failed", "turn_failed",
              "instrument_fault", "error"):
        if by.get(v):
            print(f"  {v:16s} {len(by[v]):3d}   {', '.join(sorted(by[v])[:12])}"
                  + (" …" if len(by[v]) > 12 else ""))
    slow = [r for r in rows if a.strict_seconds
            and (r.get("seconds") or 0) > a.strict_seconds]
    bad = [r for r in rows if r.get("verdict") in
           ("wasteful", "unavailable", "unverified", "unmeasured",
            "background_failed", "turn_failed", "instrument_fault", "error")]
    # ARM IT. A gate called --install that only ever asks for libraries the
    # pack already ships cannot install anything: every row comes back
    # `ready_from_pack` and the run reports 46/46 having never executed the
    # install path once. That is what shipped an environment with no C++
    # compiler and no libxml2 headers — the two failures a single real install
    # would have caught, past three green gates.
    #
    # So: unless the caller explicitly scoped to recognition
    # (--pack-provided-only), the run MUST have exercised a real install.
    installed_rows = [r for r in rows
                      if r.get("verdict") in ("installed", "installed_session",
                                              "wasteful")
                      or (r.get("envs_created") or 0) > 0
                      or (r.get("session_adds") or 0) > 0]
    unarmed = (not a.pack_provided_only) and rows and not installed_rows
    _proof = {}
    for r in rows:
        _proof[r.get("proof", "?")] = _proof.get(r.get("proof", "?"), 0) + 1
    print("  proof: " + ", ".join(f"{k}={v}" for k, v in sorted(_proof.items()))
          + "   (asserted = the platform said ready but nothing ran)")
    if a.background:
        _where = {}
        for r in rows:
            _where.setdefault(r.get("background", "n/a"), 0)
            _where[r.get("background", "n/a")] += 1
        print("  background placement: "
              + ", ".join(f"{k}={v}" for k, v in sorted(_where.items())))
    for r in bad:
        print(f"  FAIL {r['name']}: {r.get('detail') or r['verdict']}")
    if slow:
        print(f"  SLOW (> {a.strict_seconds}s): "
              + ", ".join(f"{r['name']}={r['seconds']}s" for r in slow))
    if unarmed:
        print(f"  UNARMED: {len(rows)} package(s) checked and NOT ONE was "
              f"installed — every request was already provided by the pack, "
              f"so the install path never executed. This is not a pass. "
              f"Widen the scope (drop --pack-provided-only) or say "
              f"--pack-provided-only to gate recognition ONLY.")
    return 1 if (bad or slow or unarmed) else 0


if __name__ == "__main__":
    sys.exit(main())
