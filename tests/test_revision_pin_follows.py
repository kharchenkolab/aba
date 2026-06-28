"""The promoted pin follows the revision through set_current_revision and
delete_revision (2026-06-28 fix).

Both operations historically re-anchored a Result's member `ref`s but left
`primary_evidence_id` pointing at the old revision. RunView reads
primary_evidence_id to decide which output shows the red pin, so switching
or deleting a revision could strand the pin on a now-hidden version. Both
now route through _reanchor_results_to, which moves member refs AND
primary_evidence_id.

Synthetic (no kernel). Run: .venv/bin/python tests/test_revision_pin_follows.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_pin_follows_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "pf.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db          # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402
from core.graph.entities import create_entity, get_entity     # noqa: E402
from core.graph.edges import add_edge                         # noqa: E402
from content.bio.lifecycle.revisions import (                 # noqa: E402
    set_current_revision, delete_revision,
)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _mk(title: str) -> str:
    p = os.path.join(_tmp, f"{title}.png")
    open(p, "w").write("x")
    eid = create_entity(entity_type="figure", title=title, artifact_path=p,
                        metadata={"thread_id": "default"})
    time.sleep(0.011)  # monotone created_at (1s SQLite ts resolution)
    return eid


def _chain(n: int) -> list[str]:
    ids = [_mk(f"v{i+1}") for i in range(n)]
    for i in range(1, n):
        add_edge(source_id=ids[i], target_id=ids[i - 1], rel_type="wasRevisionOf")
    return ids


def _seed_pinned_result(anchor_id: str) -> str:
    """A Result with the pin (primary_evidence_id) AND a member ref at anchor."""
    rid = create_entity(
        entity_type="result", title="The Result",
        metadata={"thread_id": "default",
                  "primary_evidence_id": anchor_id,
                  "members": [{"id": "m1", "kind": "figure", "ref": anchor_id}]})
    add_edge(source_id=rid, target_id=anchor_id, rel_type="includes")
    return rid


def _pin(rid: str) -> str:
    return (get_entity(rid).get("metadata") or {}).get("primary_evidence_id")


def _ref(rid: str) -> str:
    return ((get_entity(rid).get("metadata") or {}).get("members") or [{}])[0].get("ref")


def test_set_current_revision_moves_the_pin():
    print("\n[1] set_current_revision moves primary_evidence_id, not just member refs")
    ids = _chain(5)                 # v1..v5
    rid = _seed_pinned_result(ids[4])   # pinned at v5 (the head)
    check("seed: pin starts at v5", _pin(rid) == ids[4])

    set_current_revision(ids[1])    # → v2
    check("member ref → v2", _ref(rid) == ids[1], _ref(rid))
    check("primary_evidence_id → v2 (pin follows)", _pin(rid) == ids[1], _pin(rid))

    set_current_revision(ids[4])    # back to v5
    check("member ref restored → v5", _ref(rid) == ids[4], _ref(rid))
    check("primary_evidence_id restored → v5", _pin(rid) == ids[4], _pin(rid))


def test_delete_revision_moves_the_pin():
    print("\n[2] delete_revision moves primary_evidence_id onto the new anchor")
    ids = _chain(3)                 # v1 ← v2 ← v3
    rid = _seed_pinned_result(ids[2])   # pinned at v3 (head)
    check("seed: pin starts at v3", _pin(rid) == ids[2])

    out = delete_revision(ids[2])   # delete the head → new anchor = v2
    check("new_anchor = v2", out.get("new_anchor") == ids[1], str(out.get("new_anchor")))
    check("member ref → v2", _ref(rid) == ids[1], _ref(rid))
    check("primary_evidence_id → v2 (pin follows)", _pin(rid) == ids[1], _pin(rid))
    check("re_anchored_members records it",
          any(m.get("new_ref") == ids[1] for m in out.get("re_anchored_members") or []),
          str(out.get("re_anchored_members")))


def main() -> int:
    test_set_current_revision_moves_the_pin()
    test_delete_revision_moves_the_pin()
    print("\n" + ("ALL PASS" if not _failures else f"FAILURES: {_failures}"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
