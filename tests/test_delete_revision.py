"""delete_revision — hard-delete a revision while keeping the chain intact.

Per the entity-mgmt refactor (2026-06-08) we expose a per-version
delete operation. Tests cover the four shapes:

  - Delete the HEAD (latest): chain shrinks, no re-parenting needed.
  - Delete a MIDDLE entry: child re-parents to grandparent, chain stays
    connected (v1 ← v3 after deleting v2 from v1 ← v2 ← v3).
  - Delete the ANCHOR (oldest, referenced by member.ref): next child
    becomes the new anchor and member.ref is rewritten.
  - Single-entry chain: refused with a clear error pointing at
    "Remove from Result".

Plus: superseded siblings are ignored when computing "active children";
re-parenting edges carry a created_by='delete_revision' breadcrumb.

Run: .venv/bin/python tests/test_delete_revision.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_del_rev_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "r.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402

from core.graph.entities import create_entity, get_entity  # noqa: E402
from core.graph.edges import add_edge, edges_from, edges_to  # noqa: E402
from content.bio.lifecycle.revisions import delete_revision  # noqa: E402
from content.bio.graph.figure_history import figure_history  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _mk_figure(title, art_name) -> str:
    art = os.path.join(_tmp, art_name)
    open(art, "w").write("x")
    return create_entity(
        entity_type="figure", title=title,
        artifact_path=art,
        metadata={"thread_id": "default"},
    )


def _build_chain(n: int) -> list[str]:
    """v1 ← v2 ← ... ← vn (each pointing wasRevisionOf at previous)."""
    ids = [_mk_figure(f"v{i+1}", f"v{i+1}.png") for i in range(n)]
    for i in range(1, n):
        add_edge(source_id=ids[i], target_id=ids[i-1], rel_type="wasRevisionOf")
    return ids


def _seed_result_with_member(anchor_id: str) -> str:
    """Create a Result whose member references `anchor_id`."""
    rid = create_entity(
        entity_type="result", title="The Result",
        metadata={"thread_id": "default",
                  "members": [{"id": "m1", "kind": "figure", "ref": anchor_id}]},
    )
    add_edge(source_id=rid, target_id=anchor_id, rel_type="includes")
    return rid


def main() -> int:
    # ── 1. Delete the chain HEAD ─────────────────────────────────────
    print("delete HEAD of chain (v1 ← v2 ← v3, delete v3)")
    v1, v2, v3 = _build_chain(3)
    rid = _seed_result_with_member(v1)
    r = delete_revision(v3)
    check("deleted reported", r.get("deleted") == v3, str(r))
    check("no children re-parented (head had none)",
          r.get("re_parented_children") == [], str(r))
    check("no members re-anchored (anchor untouched)",
          r.get("re_anchored_members") == [], str(r))
    check("v3 gone from db", get_entity(v3) is None)
    check("v1 and v2 still present",
          bool(get_entity(v1)) and bool(get_entity(v2)))
    chain = figure_history(v1)
    chain_ids = [c["id"] for c in chain]
    check("chain shrank to 2 entries", len(chain) == 2, str(chain_ids))
    check("v3 not in chain", v3 not in chain_ids, str(chain_ids))
    check("chain head is now v2",
          chain[0]["id"] == v2, f"chain={chain_ids}")
    # Member.ref unchanged (still pointing at anchor)
    r_ent = get_entity(rid)
    members = (r_ent.get("metadata") or {}).get("members") or []
    check("member.ref still v1 (anchor unchanged)",
          members and members[0].get("ref") == v1, str(members))

    # ── 2. Delete a MIDDLE entry ─────────────────────────────────────
    print("delete MIDDLE of chain (v1 ← v2 ← v3, delete v2)")
    v1, v2, v3 = _build_chain(3)
    rid = _seed_result_with_member(v1)
    r = delete_revision(v2)
    check("v2 deleted", get_entity(v2) is None)
    check("v3 re-parented to v1",
          r.get("re_parented_children") == [v3] and r.get("new_parent") == v1,
          str(r))
    # Confirm the actual edge moved
    v3_edges_out = [e for e in edges_from(v3) if e["rel_type"] == "wasRevisionOf"]
    check("v3 wasRevisionOf edge → v1",
          len(v3_edges_out) == 1 and v3_edges_out[0]["target_id"] == v1,
          str(v3_edges_out))
    # Breadcrumb in edge metadata
    md = v3_edges_out[0].get("metadata") or {} if v3_edges_out else {}
    check("re-parent edge carries created_by=delete_revision",
          md.get("created_by") == "delete_revision", str(md))
    # Chain still connects v1 ← v3
    chain = figure_history(v1)
    chain_ids = [c["id"] for c in chain]
    check("chain has 2 entries (v1, v3)",
          set(chain_ids) == {v1, v3}, str(chain_ids))
    # Member.ref still v1
    r_ent = get_entity(rid)
    members = (r_ent.get("metadata") or {}).get("members") or []
    check("member.ref still v1",
          members and members[0].get("ref") == v1, str(members))

    # ── 3. Delete the ANCHOR (referenced by member.ref) ──────────────
    print("delete ANCHOR (v1 ← v2 ← v3, delete v1)")
    v1, v2, v3 = _build_chain(3)
    rid = _seed_result_with_member(v1)
    r = delete_revision(v1)
    check("v1 deleted", get_entity(v1) is None)
    check("v1 had no parent (anchor)", r.get("new_parent") is None)
    check("first child v2 became new anchor",
          r.get("new_anchor") == v2, str(r))
    # v2 should have NO wasRevisionOf edge now (it was the only child, no grandparent)
    v2_edges_out = [e for e in edges_from(v2) if e["rel_type"] == "wasRevisionOf"]
    check("v2 has no wasRevisionOf edge after losing parent",
          v2_edges_out == [], str(v2_edges_out))
    # Member.ref rewritten to v2
    r_ent = get_entity(rid)
    members = (r_ent.get("metadata") or {}).get("members") or []
    check("member.ref rewritten to v2",
          members and members[0].get("ref") == v2, str(members))
    check("re_anchored_members records the change",
          r.get("re_anchored_members") and
          r["re_anchored_members"][0]["new_ref"] == v2, str(r))
    # Chain from new anchor still walks to v3
    chain = figure_history(v2)
    chain_ids = [c["id"] for c in chain]
    check("chain from v2 reaches v3",
          set(chain_ids) == {v2, v3}, str(chain_ids))
    # Result has includes edge to v2 (re-added by delete_revision)
    inc_targets = [e["target_id"] for e in edges_from(rid)
                   if e["rel_type"] == "includes"]
    check("Result --includes--> v2 edge exists",
          v2 in inc_targets, str(inc_targets))

    # ── 4. Single-entry chain refuses ─────────────────────────────────
    print("single-version chain refuses")
    lonely = _mk_figure("solo", "solo.png")
    try:
        delete_revision(lonely)
        check("single-version refused", False, "no exception raised")
    except ValueError as e:
        msg = str(e)
        check("single-version refused with helpful message",
              "only active version" in msg and "Remove from Result" in msg,
              msg)
    check("solo entity NOT deleted",
          get_entity(lonely) is not None)

    # ── 5. Wrong type refuses ─────────────────────────────────────────
    print("non-figure/table refuses")
    res_id = create_entity(entity_type="result", title="x",
                           metadata={"thread_id": "default"})
    try:
        delete_revision(res_id)
        check("non-figure/table refused", False, "no exception")
    except ValueError as e:
        check("non-figure/table refused with type-named message",
              "figure/table" in str(e), str(e))

    # ── 6. Unknown id refuses ─────────────────────────────────────────
    print("unknown id refuses")
    try:
        delete_revision("fig_nonexistent")
        check("unknown id refused", False, "no exception")
    except ValueError as e:
        check("unknown id refused with not-found message",
              "not found" in str(e), str(e))

    # ── 7. Superseded children don't count as "active children" ──────
    print("superseded siblings ignored when counting active children")
    v1, v2, v3 = _build_chain(3)
    # Pretend v3 was rolled back (status=superseded)
    from core.graph.entities import update_entity
    update_entity(v3, status="superseded")
    # Now delete v2 — its only child v3 is superseded, so re_parented
    # should be empty (we don't move superseded edges)
    r = delete_revision(v2)
    check("no active children re-parented when only child is superseded",
          r.get("re_parented_children") == [], str(r))
    # v3 is still there with its (now-dangling) wasRevisionOf edge.
    check("v3 (superseded) still present", get_entity(v3) is not None)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL DELETE-REVISION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
