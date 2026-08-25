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
        if t == "run_started":
            cap["run_id"] = ev.get("run_id") or cap.get("run_id")
        elif t == "tool_call":
            cap["tools"].append(ev.get("name"))
        elif t in ("error", "cancelled"):
            cap["errors"].append(str(ev)[:300])
        elif t == "text":
            cap["text"].append(str(ev.get("text") or ""))


def _env_count(projects_dir: Path | None, pid: str) -> int | None:
    """Named/isolated envs recorded for a project, or None if unreadable.

    None is not zero: an unmeasured ceiling must fail, never pass."""
    if projects_dir is None:
        return None
    p = Path(projects_dir) / str(pid) / "weft_envs.json"
    if not p.exists():
        return 0
    try:
        return len((json.loads(p.read_text()) or {}).get("envs") or {})
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
              pack_names) -> dict:
    name = entry["name"]
    slug = "".join(ch if ch.isalnum() else "-" for ch in name).lower()[:40]
    # One bad entry must not end a 100+ package sweep: record and move on.
    try:
        pid = c.post("/api/projects", json={"name": f"install-{slug}"}).json().get("id")
        c.post(f"/api/projects/{pid}/open")
        tid = c.post("/api/threads",
                     json={"project_id": pid, "title": f"install-{slug}"}).json().get("id")
    except Exception as e:  # noqa: BLE001
        return {**entry, "verdict": "error",
                "detail": f"setup: {type(e).__name__}: {e}"[:300]}
    if not pid or not tid:
        return {**entry, "verdict": "error", "detail": "project/thread creation failed"}

    envs_before = _env_count(projects_dir, pid)
    cap = {"run_id": None, "tools": [], "errors": [], "text": []}
    t0 = time.time()
    try:
        with c.stream("POST", "/api/chat", timeout=timeout,
                      json={"text": _prompt_for(entry), "project_id": pid,
                            "thread_id": tid}) as r:
            r.raise_for_status()
            _consume(r, cap)
        for _ in range(6):
            rid = cap["run_id"]
            if not rid:
                break
            st = c.get(f"/api/turns/{rid}").json().get("state")
            if st != "awaiting_user":
                break
            with c.stream("POST", f"/api/turns/{rid}/resume", timeout=timeout,
                          json={"user_text": "Yes, go ahead."}) as r2:
                r2.raise_for_status()
                _consume(r2, cap)
    except Exception as e:  # noqa: BLE001 — a wedged turn is a finding, not a crash
        return {**entry, "verdict": "error", "seconds": round(time.time() - t0, 1),
                "detail": f"{type(e).__name__}: {e}"[:300]}
    seconds = round(time.time() - t0, 1)
    envs_after = _env_count(projects_dir, pid)
    made = None if (envs_before is None or envs_after is None) else envs_after - envs_before

    try:
        ents = c.get("/api/entities",
                     params={"project_id": pid, "include_archived": True}).json()
        ents = ents if isinstance(ents, list) else ents.get("entities", [])
        runs = [e["id"] for e in ents if e.get("type") == "analysis"]
        ok, exec_detail = _exec_ok(c, pid, runs)
    except Exception as e:  # noqa: BLE001
        return {**entry, "verdict": "error", "seconds": seconds,
                "detail": f"state read: {type(e).__name__}: {e}"[:300]}

    row = {**entry, "project_id": pid, "seconds": seconds,
           "envs_created": made, "tools": len(cap["tools"]),
           "turn_errors": len(cap["errors"]), "exec": exec_detail}
    provided = name in pack_names
    row["pack_provided"] = provided
    ceiling = PACK_ENV_CEILING if provided else INSTALL_ENV_CEILING

    if not ok:
        row["verdict"] = "unavailable"
        row["detail"] = ("nothing executed cleanly for this request — "
                         + exec_detail)
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
    row["verdict"] = "ready_from_pack" if made == 0 else "installed"
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
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--results", default=None,
                    help="JSON file; existing entries are SKIPPED (resumable)")
    ap.add_argument("--pack-provided-only", action="store_true",
                    help="only the names a shipped base pack proves it loads")
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
    if a.limit:
        entries = entries[:a.limit]

    print(f"[install-probe] {len(entries)} to run "
          f"({len(done)} already recorded), pack-provided names known: "
          f"{len(pack_names)}", flush=True)

    rows = list(done.values())
    with httpx.Client(base_url=a.base, timeout=a.timeout) as c:
        for i, e in enumerate(entries, 1):
            print(f"[install-probe] {i}/{len(entries)} {e['name']} "
                  f"({e.get('ecosystem')})…", flush=True)
            row = probe_one(c, e, timeout=a.timeout,
                            projects_dir=Path(a.projects_dir) if a.projects_dir else None,
                            pack_names=pack_names)
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
    for v in ("ready_from_pack", "installed", "wasteful", "unavailable",
              "unmeasured", "error"):
        if by.get(v):
            print(f"  {v:16s} {len(by[v]):3d}   {', '.join(sorted(by[v])[:12])}"
                  + (" …" if len(by[v]) > 12 else ""))
    slow = [r for r in rows if a.strict_seconds
            and (r.get("seconds") or 0) > a.strict_seconds]
    bad = [r for r in rows if r.get("verdict") in
           ("wasteful", "unavailable", "unmeasured", "error")]
    for r in bad:
        print(f"  FAIL {r['name']}: {r.get('detail') or r['verdict']}")
    if slow:
        print(f"  SLOW (> {a.strict_seconds}s): "
              + ", ".join(f"{r['name']}={r['seconds']}s" for r in slow))
    return 1 if (bad or slow) else 0


if __name__ == "__main__":
    sys.exit(main())
