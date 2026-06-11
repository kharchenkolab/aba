"""get_provenance must surface long chains, not silently truncate.

Live bug 2026-06-11 (prj_128380fd thr_deed230d): the agent asked for
provenance on a 7-revision figure and got back only the 3 most-recent
ancestors. With no `max_depth` parameter on the tool surface, there was
no way to look further. This test pins:

  - default depth raised from 3 to 8 on the agent's get_provenance MCP
    tool (the live bug repros at depth=3 — at depth=8 a 7-revision
    chain returns every ancestor),
  - `max_depth` is honored when passed explicitly.

Run: .venv/bin/python tests/test_provenance_depth.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_prov_depth_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "p.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]    = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402

from core.graph.entities import create_entity  # noqa: E402
from core.graph.edges import add_edge          # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _chain(n: int) -> list[str]:
    """v1 ← v2 ← ... ← vn, returned oldest-first."""
    ids = []
    for i in range(n):
        p = os.path.join(_tmp, f"d_{i}.png")
        open(p, "w").write("x")
        ids.append(create_entity(
            entity_type="figure", title=f"chain entry {i+1}",
            artifact_path=p, metadata={"thread_id": "default"},
        ))
    for i in range(1, n):
        add_edge(source_id=ids[i], target_id=ids[i-1], rel_type="wasRevisionOf")
    return ids


def main() -> int:
    # ── 7-revision chain ─────────────────────────────────────────────
    print("7-revision chain (the live-bug shape)")
    ids = _chain(7)
    head = ids[-1]

    # 1. Default behavior of the MCP-tool entry point: depth=8
    print("\n  agent-facing tool default reaches all 6 ancestors")
    from content.bio.tools.ctx_read import get_provenance
    out = get_provenance({"entity_id": head})
    graph = out.get("graph") or []
    check("default depth surfaces all 6 ancestors",
          len(graph) == 6,
          f"got {len(graph)}: {[g.get('id') for g in graph]}")
    # And the text mention count too — one bullet per ancestor.
    txt = out.get("text") or ""
    bullets = [l for l in txt.splitlines() if l.lstrip().startswith("-")]
    check("text has 6 bullets",
          len(bullets) == 6, f"got {len(bullets)}: {bullets}")

    # 2. The OLD default (depth=3) only saw 3 ancestors — confirm the
    #    regression case so a future depth tweak can't silently hide
    #    the chain again.
    print("\n  explicit depth=3 still truncates (regression guard)")
    out3 = get_provenance({"entity_id": head, "max_depth": 3})
    check("depth=3 caps at 3 ancestors",
          len(out3.get("graph") or []) == 3,
          f"got {len(out3.get('graph') or [])}")

    # 3. Raising the cap further is fine
    print("\n  depth=20 still returns 6 (chain is shorter than cap)")
    out20 = get_provenance({"entity_id": head, "max_depth": 20})
    check("depth=20 returns all 6 (chain is the limit, not depth)",
          len(out20.get("graph") or []) == 6)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL PROVENANCE-DEPTH CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
