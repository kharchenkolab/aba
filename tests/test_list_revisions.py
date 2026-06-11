"""list_revisions — turn a figure/table id into the labeled chain the
user sees in the chevron strip.

Background: in a live session (prj_128380fd thr_deed230d, 2026-06-11)
the user asked the agent to "come back to version 6". The agent had no
direct way to map "v6" to an entity id — get_provenance was depth-3
capped, list_entities title-search whiffed, and titles in the chain
are all identical. It ended up guessing from conversation memory and
then walking destructively backwards via delete_revision.

list_revisions is the addressing primitive that gap calls for: it
wraps figure_history() and labels every entry with the v1…vN number
RevisionStrip.tsx:242 shows the user.

Run: .venv/bin/python tests/test_list_revisions.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_list_rev_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "lr.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402

from core.graph.entities import create_entity, update_entity  # noqa: E402
from core.graph.edges import add_edge                            # noqa: E402
from content.bio.lifecycle.revisions import list_revisions       # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _mk(title: str, art_name: str) -> str:
    p = os.path.join(_tmp, art_name)
    open(p, "w").write("x")
    return create_entity(
        entity_type="figure", title=title, artifact_path=p,
        metadata={"thread_id": "default"},
    )


def _chain(n: int, *, label="v") -> list[str]:
    ids = [_mk(label, f"{label}_{i}.png") for i in range(n)]
    for i in range(1, n):
        add_edge(source_id=ids[i], target_id=ids[i-1], rel_type="wasRevisionOf")
    return ids


def main() -> int:
    # ── 1. Labels: oldest is v1, newest is vN ────────────────────────
    print("3-revision chain, labels match RevisionStrip (oldest=v1, newest=vN)")
    ids = _chain(3, label="A")
    out = list_revisions(ids[1])  # pass a middle id — must resolve full chain

    check("total = 3", out.get("total") == 3, str(out))
    check("current_id = newest", out.get("current_id") == ids[2], str(out))
    revs = out.get("revisions") or []
    check("3 revisions returned", len(revs) == 3)
    if len(revs) == 3:
        # newest-first ordering, mirroring the chevron strip
        check("revs[0].version == 3 (newest)", revs[0]["version"] == 3)
        check("revs[0].id == newest id",  revs[0]["id"] == ids[2])
        check("revs[0].is_current == True", revs[0]["is_current"] is True)
        check("revs[2].version == 1 (oldest)", revs[2]["version"] == 1)
        check("revs[2].id == oldest id",  revs[2]["id"] == ids[0])
        check("revs[2].is_current == False", revs[2]["is_current"] is False)
        # exec_id field surfaces (None when none was set, but the key
        # must be present so the agent knows it can compare runs).
        check("exec_id key present on every rev",
              all("exec_id" in r for r in revs))

    # ── 2. Single-entry chain ────────────────────────────────────────
    print("\nsingle figure (no revisions yet)")
    lonely = _mk("solo", "solo.png")
    out = list_revisions(lonely)
    check("total = 1",  out.get("total") == 1)
    check("current_id = self", out.get("current_id") == lonely)
    revs = out.get("revisions") or []
    check("v1 is_current", revs and revs[0]["version"] == 1 and revs[0]["is_current"])

    # ── 3. Superseded sibling is skipped (mirrors UI behaviour) ──────
    print("\nbranched chain with superseded sibling — UI sees linear chain")
    # Build v1 ← v2 ← v3, then a sibling v3' off v2 that's superseded.
    ids = _chain(3, label="B")
    sib = _mk("B", "B_sib.png")
    add_edge(source_id=sib, target_id=ids[1], rel_type="wasRevisionOf")
    update_entity(sib, status="superseded")
    out = list_revisions(ids[0])
    check("superseded sibling skipped (total stays 3)",
          out.get("total") == 3, str(out))
    seen_ids = {r["id"] for r in (out.get("revisions") or [])}
    check("superseded sibling NOT in revisions",
          sib not in seen_ids, str(seen_ids))

    # ── 4. Wrong type refused ────────────────────────────────────────
    print("\nrejects non-figure/table entity")
    not_fig = create_entity(entity_type="result", title="r",
                            metadata={"thread_id": "default"})
    try:
        list_revisions(not_fig)
        check("rejected non-figure", False, "no ValueError raised")
    except ValueError:
        check("rejected non-figure", True)

    # ── 5. Unknown id refused ────────────────────────────────────────
    print("\nrejects unknown id")
    try:
        list_revisions("fig_does_not_exist")
        check("rejected unknown id", False)
    except ValueError:
        check("rejected unknown id", True)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL LIST-REVISIONS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
