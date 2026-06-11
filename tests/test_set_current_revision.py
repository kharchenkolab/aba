"""set_current_revision — non-destructive 'go back to vN'.

Background: in prj_128380fd thr_deed230d (2026-06-11) the user said
"come back to version 6". The agent had no non-destructive switch tool
and resorted to four delete_revision calls — wiping v7…v10 from the
chain permanently. set_current_revision is the safe alternative: mark
newer revisions as superseded (hidden from the chevron strip but kept
on disk + reversible), restore older ones if a previous switch hid
them, and re-anchor any Result members whose ref points at the now-
superseded entries.

Run: .venv/bin/python tests/test_set_current_revision.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_set_cur_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "sc.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402

import time                                           # noqa: E402
from core.graph.entities import create_entity, get_entity, update_entity   # noqa: E402
from core.graph.edges import add_edge                                       # noqa: E402
from content.bio.lifecycle.revisions import (                               # noqa: E402
    set_current_revision, list_revisions,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _mk(title: str, art_name: str) -> str:
    p = os.path.join(_tmp, art_name)
    open(p, "w").write("x")
    eid = create_entity(
        entity_type="figure", title=title, artifact_path=p,
        metadata={"thread_id": "default"},
    )
    # figure_history(include_superseded=True) orders by created_at DESC —
    # SQLite's TIMESTAMP resolution is 1s, so back-to-back create_entity
    # calls can tie. Sleep 1ms+ to guarantee monotone created_at.
    time.sleep(0.011)
    return eid


def _chain(n: int) -> list[str]:
    """v1 ← v2 ← … ← vN, returned oldest-first."""
    ids = [_mk(f"v{i+1}", f"v{i+1}.png") for i in range(n)]
    for i in range(1, n):
        add_edge(source_id=ids[i], target_id=ids[i-1], rel_type="wasRevisionOf")
    return ids


def _seed_result(anchor_id: str) -> str:
    rid = create_entity(
        entity_type="result", title="The Result",
        metadata={"thread_id": "default",
                  "members": [{"id": "m1", "kind": "figure", "ref": anchor_id}]},
    )
    add_edge(source_id=rid, target_id=anchor_id, rel_type="includes")
    return rid


def _member_ref(rid: str) -> str:
    return ((get_entity(rid).get("metadata") or {})
            .get("members") or [{}])[0].get("ref")


def main() -> int:
    # ── 1. Happy path: 7-version chain, jump to v4 ───────────────────
    print("7-revision chain, set_current_revision → v4 (the live-bug shape)")
    ids = _chain(7)        # ids[0]=v1 (oldest), ids[6]=v7 (newest)
    rid = _seed_result(ids[6])  # member.ref starts at the newest (v7)

    out = set_current_revision(ids[3])  # → v4
    check("current_id = v4 id", out.get("current_id") == ids[3], str(out))
    # v5, v6, v7 should be superseded (3 ids); none restored (chain
    # was clean to start with).
    superseded = set(out.get("superseded") or [])
    expected_superseded = {ids[6], ids[5], ids[4]}
    check("v5,v6,v7 newly superseded",
          superseded == expected_superseded,
          f"got {superseded}, expected {expected_superseded}")
    check("nothing restored on first switch (no prior supersession)",
          out.get("restored") == [], str(out))
    check("member re-anchored from v7 → v4",
          _member_ref(rid) == ids[3],
          f"got ref={_member_ref(rid)}")

    # list_revisions now sees v1..v4 (chain head = v4)
    print("\n  list_revisions reflects the switch")
    lr = list_revisions(ids[0])
    check("chain total now 4", lr.get("total") == 4,
          f"got total={lr.get('total')}")
    check("v4 is current_id", lr.get("current_id") == ids[3])
    versions_seen = {r["version"] for r in (lr.get("revisions") or [])}
    check("revisions numbered 1..4", versions_seen == {1, 2, 3, 4},
          f"got {versions_seen}")

    # ── 2. Reversibility: jump back to v7 ────────────────────────────
    print("\n  reversible: set_current_revision → v7 restores the hidden ones")
    out = set_current_revision(ids[6])
    restored = set(out.get("restored") or [])
    check("v5,v6,v7 all restored", restored == {ids[6], ids[5], ids[4]},
          f"got {restored}")
    check("nothing newly superseded (v7 is at the head already)",
          out.get("superseded") == [], str(out))
    lr = list_revisions(ids[0])
    check("chain total back to 7", lr.get("total") == 7,
          f"got total={lr.get('total')}")
    check("member re-points at v7", _member_ref(rid) == ids[6],
          f"got ref={_member_ref(rid)}")
    # Sanity: every previously-superseded entry is active again.
    for i in (4, 5, 6):
        check(f"v{i+1} status active again",
              get_entity(ids[i]).get("status") == "active",
              f"v{i+1} status={get_entity(ids[i]).get('status')}")

    # ── 3. Idempotency: same target twice is a no-op the second time ──
    print("\n  idempotent: second call on the same target reports no changes")
    set_current_revision(ids[2])    # switch to v3
    out2 = set_current_revision(ids[2])  # again
    check("second call superseded list empty",
          out2.get("superseded") == [], str(out2))
    check("second call restored list empty",
          out2.get("restored") == [], str(out2))

    # ── 4. Wrong type refused ────────────────────────────────────────
    print("\n  refuses non-figure/table entity")
    rid_only = create_entity(entity_type="result", title="solo",
                             metadata={"thread_id": "default"})
    try:
        set_current_revision(rid_only)
        check("rejects non-figure/table", False, "no ValueError raised")
    except ValueError:
        check("rejects non-figure/table", True)

    # ── 5. Single-entry chain is a no-op, not an error ──────────────
    print("\n  single-entry chain: returns OK with no changes")
    lonely = _mk("solo-fig", "solo.png")
    out3 = set_current_revision(lonely)
    check("single-entry: current_id = self",
          out3.get("current_id") == lonely)
    check("single-entry: total_in_chain = 1",
          out3.get("total_in_chain") == 1)
    check("single-entry: nothing superseded/restored",
          out3.get("superseded") == [] and out3.get("restored") == [])

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL SET-CURRENT-REVISION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
