#!/usr/bin/env python
"""Project-isolation audit — every recorded row belongs to a thread of the
project whose DB holds it.

The failure this catches (live, 2026-07-27, two workflow sweeps interleaved):
`loop.run_in_executor` does not carry contextvars, so past the tool-dispatch hop
the project binding was lost and project-scoped writes resolved to the
PROCESS-GLOBAL project. One remote run's execution_records, harvest directory and
registered artifacts landed in a bystander project while its messages went to the
right one. The producing project ended with ZERO provenance for a run it really
performed; the bystander gained a file it never produced, and `find_files` there
listed it as its own.

Nothing in the per-project view looks wrong — each row is individually
well-formed. The corruption is only visible ACROSS projects, which is why it
needs its own audit rather than a scenario check. Cheap and read-only: run it
after any multi-project or concurrent live run.

    python regtest/harness/project_isolation.py [--since-minutes 120] [--all]

Exit 0 clean, 1 on any violation, 2 on setup error.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

RUNTIME = Path.home() / ".aba" / "runtime" / "projects"


def _threads_of(db: Path) -> set[str]:
    """Thread ids this project's MESSAGES belong to — the authority on
    membership, because messages are written on the event loop and were the one
    thing the bug never corrupted."""
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        return {r[0] for r in c.execute("select distinct thread_id from messages")
                if r[0]}
    except Exception:  # noqa: BLE001
        return set()


def _exec_rows(db: Path) -> list[tuple]:
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        return list(c.execute(
            "select exec_id, thread_id, record_path, started_at "
            "from execution_records"))
    except Exception:  # noqa: BLE001
        return []


def audit(projects: dict[str, dict]) -> list[str]:
    """Pure rule → violations. `projects` maps pid → {threads, execs}.

    Two independent doors, because either alone can read as green:
      * a row whose thread belongs to ANOTHER project (the leak, seen from the
        receiving side);
      * a row whose sidecar path names a DIFFERENT project than the DB holding
        it (the same leak seen on disk — it also catches the case where the
        thread is unknown everywhere, e.g. the project was since deleted).
    """
    owner: dict[str, str] = {}
    for pid, info in projects.items():
        for tid in info["threads"]:
            owner[tid] = pid

    out: list[str] = []
    for pid, info in projects.items():
        for exec_id, tid, record_path, started in info["execs"]:
            if tid and tid in owner and owner[tid] != pid:
                out.append(
                    f"{pid}: exec {exec_id} belongs to thread {tid} of "
                    f"{owner[tid]} (started {started})")
            rp = str(record_path or "")
            if "/projects/" in rp:
                named = rp.split("/projects/", 1)[1].split("/", 1)[0]
                if named and named != pid:
                    out.append(
                        f"{pid}: exec {exec_id} sidecar written under "
                        f"{named} ({rp[:90]})")
    return out


def collect(since_ts: float | None) -> dict[str, dict]:
    projects: dict[str, dict] = {}
    for p in sorted(RUNTIME.iterdir()):
        db = p / "project.db"
        if not db.exists():
            continue
        if since_ts and db.stat().st_mtime < since_ts:
            continue
        projects[p.name] = {"threads": _threads_of(db), "execs": _exec_rows(db)}
    return projects


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-minutes", type=int, default=180,
                    help="only projects touched this recently (0/--all = every project)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not RUNTIME.is_dir():
        print(f"[isolation] SETUP-ERROR: {RUNTIME} not found", file=sys.stderr)
        return 2
    since = None if (a.all or not a.since_minutes) else time.time() - a.since_minutes * 60
    projects = collect(since)
    if not projects:
        print("[isolation] SETUP-ERROR: no projects in scope — widen --since-minutes",
              file=sys.stderr)
        return 2

    n_exec = sum(len(v["execs"]) for v in projects.values())
    # ARMED: an audit over projects that recorded NO executions proves nothing.
    # Say so rather than printing a clean verdict.
    if n_exec == 0:
        print(f"[isolation] SETUP-ERROR: {len(projects)} project(s) in scope but "
              f"ZERO execution records — nothing to audit", file=sys.stderr)
        return 2

    bad = audit(projects)
    if a.json:
        print(json.dumps({"projects": len(projects), "execs": n_exec,
                          "violations": bad}, indent=2))
    else:
        print(f"[isolation] {len(projects)} project(s), {n_exec} execution record(s)")
        if bad:
            print(f"[isolation] FAIL — {len(bad)} violation(s):")
            for b in bad:
                print(f"  - {b}")
        else:
            print("[isolation] PASS — every execution record belongs to a thread "
                  "of the project holding it, and every sidecar is under it")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
