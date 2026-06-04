"""Block 1A: every (src_type, target_type, rel) tuple that bio code's
`add_edge()` call sites actually write must be declared in the source's
`allowed_edges.out` and the target's `allowed_edges.in`. Otherwise the
Phase 4.5 validators warn on EVERY real write — wasted noise.

This test enumerates the writes that previously fired warnings during
real use (2026-06-03 prj_ee6d95e8 — promote_to_result wraps a pinned
figure as a Result member via `includes` + `wasDerivedFrom`) AND covers
the non-obvious edges from bio/lifecycle/promote.py and bio/web/routes.py.

Wire it up so the registry's `valid_edge()` returns True for each. If
this test fails, the call site + YAML have drifted apart — fix the YAML
or remove the dead call site.

The list isn't exhaustive of ALL edges in code — it's the set surfaced
by live use that the validators caught. Extending it as new warnings
appear is the maintenance loop.

Run:
    .venv/bin/python tests/p10_yaml_edges_cover_code.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio  # noqa: E402, F401  -- triggers load_types()
from core.entity_types import valid_edge  # noqa: E402


# Tuples are (source_type, target_type, rel) — every triple here has at
# least one add_edge() call site in code. Grouped by call site for
# traceability when something fails.
EDGES_FROM_CODE = [
    # bio/lifecycle/promote.py:64  evidence --produced_by--> run
    ("figure",    "analysis", "produced_by"),
    ("table",     "analysis", "produced_by"),
    # bio/lifecycle/promote.py:83-84 / 126-128 — result wraps evidence
    ("result",    "figure",    "includes"),
    ("result",    "table",     "includes"),
    ("result",    "note",      "includes"),
    ("result",    "narrative", "includes"),
    ("result",    "figure",    "supports"),
    ("result",    "table",     "supports"),
    ("result",    "note",      "supports"),
    ("result",    "narrative", "supports"),
    ("result",    "figure",    "wasDerivedFrom"),
    ("result",    "table",     "wasDerivedFrom"),
    ("result",    "note",      "wasDerivedFrom"),
    ("result",    "narrative", "wasDerivedFrom"),
    # bio/lifecycle/promote.py:130  result --wasDerivedFrom--> run
    ("result",    "analysis",  "wasDerivedFrom"),
    # bio/lifecycle/promote.py:573-574  result --supports/wasDerivedFrom--> figure
    # (already in result→figure rows above)
    # bio/lifecycle/promote.py:599-600/618-619  finding --supports/wasDerivedFrom--> result
    ("finding",   "result",    "supports"),
    ("finding",   "result",    "wasDerivedFrom"),
    # bio/lifecycle/promote.py:664-665  finding --supports/wasDerivedFrom--> evidence
    ("finding",   "figure",    "supports"),
    ("finding",   "figure",    "wasDerivedFrom"),
    ("finding",   "table",     "supports"),
    ("finding",   "table",     "wasDerivedFrom"),
    # bio/lifecycle/promote.py:713-714  claim --supports/wasDerivedFrom--> finding
    ("claim",     "finding",   "supports"),
    ("claim",     "finding",   "wasDerivedFrom"),
    # bio/web/routes.py:142,166  claim --supports--> result
    ("claim",     "result",    "supports"),
    # bio/lifecycle/registry.py:153,178  figure/table --wasGeneratedBy--> analysis
    ("figure",    "analysis",  "wasGeneratedBy"),
    ("table",     "analysis",  "wasGeneratedBy"),
    # bio/lifecycle/registry.py:156  analysis --used--> dataset/focus
    ("analysis",  "dataset",   "used"),
    # bio/lifecycle/registry.py:157,181  figure/result --wasDerivedFrom--> focus
    ("figure",    "dataset",   "wasDerivedFrom"),
    # bio/lifecycle/scenarios.py:114  figure --variantOf--> figure
    ("figure",    "figure",    "variantOf"),
    # bio/web/routes.py:741  dataset --produced_by--> run
    ("dataset",   "analysis",  "produced_by"),
    # bio/proposals/scheduler.py:401 / tools.py:3573 — claim --supports--> result
    # (already covered above)
]


def test_every_code_edge_is_declared():
    failed = []
    for src, tgt, rel in EDGES_FROM_CODE:
        if not valid_edge(src, tgt, rel):
            failed.append(f"{src} --{rel}--> {tgt}")
    if failed:
        msg = (f"{len(failed)} edges written by add_edge() in bio code but "
               f"not declared in YAML allowed_edges:\n  " + "\n  ".join(failed))
        raise AssertionError(msg)


def test_undeclared_edges_still_rejected():
    """Sanity: we didn't open allowed_edges so wide that bogus edges pass."""
    # workspace has out: [] — it shouldn't support anything.
    assert not valid_edge("workspace", "claim",  "supports")
    # plan has no edges declared.
    assert not valid_edge("plan",      "figure", "supports")
    # invented rel: never legitimate.
    assert not valid_edge("figure",    "claim",  "is_better_than")
    # cross-type oddity: claim doesn't "use" a dataset.
    assert not valid_edge("claim",     "dataset", "used")


def main() -> int:
    tests = [
        test_every_code_edge_is_declared,
        test_undeclared_edges_still_rejected,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}:\n{e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
