#!/usr/bin/env python
"""Which env packs did a DRIVE actually realize? — observed, not re-derived.

WHY THIS IS NOT `resolve the pins again`. A deployment's pins are a claim about
what it will run. Re-reading them at the end of a gate and writing them down as
"what was verified" is the shape that keeps failing here: the instrument hands
the subject its own answer, and agrees with itself no matter what happened. The
question worth recording is what the RUNNING SERVER adopted while the lanes
drove it.

weft answers that. Its workspace SQLite (`<ABA_HOME>/weft/.weft/state.db`)
carries a `realizations` row per env it made ready, keyed by EnvID; the store's
`catalog.json` carries the EnvID of every published pack version. Intersect the
two and you have pack -> version for exactly the packs this drive stood on,
sourced from two records that were written independently of each other.

    drove_packs.py --workspace <ABA_HOME> --tree <env store>
      python-bio=2026.08.27-8d4389ba
      r-bio=2026.08.27-654521e9

Exit 0 with at least one pair, or exit 2 having said why. NEVER exit 0 with an
empty list: a drive that adopted no pack at all is a broken gate, not a
deployment with no packs — and "nothing observed" must not read the same as
"nothing to observe".
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

OK, FATAL = 0, 2


def realized_env_ids(workspace: str) -> set[str]:
    db = Path(workspace) / "weft" / ".weft" / "state.db"
    if not db.is_file():
        raise FileNotFoundError(f"no weft state at {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT env_id FROM realizations WHERE state = 'ready'").fetchall()
    finally:
        con.close()
    return {r[0] for r in rows if r and r[0]}


def catalog_index(tree: str) -> dict[str, tuple[str, str]]:
    """EnvID -> (pack, version), from the store's own records."""
    envs = json.loads((Path(tree) / "catalog.json").read_text())["envs"]
    out: dict[str, tuple[str, str]] = {}
    for pack, entry in envs.items():
        for version, meta in (entry.get("versions") or {}).items():
            eid = meta.get("env_id")
            if eid:
                out[eid] = (pack, version)
    return out


def drove(workspace: str, tree: str) -> tuple[int, list[str], list[str]]:
    """-> (exit_code, pairs, notes). Pure, so the tests drive it directly."""
    notes: list[str] = []
    try:
        realized = realized_env_ids(workspace)
    except Exception as e:  # noqa: BLE001
        return FATAL, [], [f"cannot read the drive's weft workspace: {e}"]
    if not realized:
        return FATAL, [], ["the drive realized NO environment at all. Either the "
                           "lanes never ran anything, or they ran against a "
                           "workspace other than the one named here."]
    try:
        index = catalog_index(tree)
    except Exception as e:  # noqa: BLE001
        return FATAL, [], [f"cannot read the env store at {tree}: {e}"]

    pairs, unknown = [], 0
    for eid in sorted(realized):
        hit = index.get(eid)
        if hit:
            pairs.append(f"{hit[0]}={hit[1]}")
        else:
            unknown += 1
    if unknown:
        # Expected and fine: session-scope envs the agent built during the lanes
        # are realized too and are not published packs. Counted, not hidden.
        notes.append(f"{unknown} realized env(s) are not published packs "
                     f"(session-scope work) — not recorded as pins")
    if not pairs:
        return FATAL, [], notes + [
            "the drive realized environments, but NONE of them is a published "
            "pack in this store. A deployment always stands on its base pack, "
            "so this is a store/workspace mismatch, not an empty answer."]
    return OK, sorted(set(pairs)), notes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", required=True, help="the drive's ABA_HOME")
    ap.add_argument("--tree", required=True, help="the shared env store")
    a = ap.parse_args()
    rc, pairs, notes = drove(a.workspace, a.tree)
    for n in notes:
        print(f"# {n}", file=sys.stderr)
    for p in pairs:
        print(p)
    if rc == FATAL:
        print("drove_packs: refusing to report an empty set as 'no packs'.",
              file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
