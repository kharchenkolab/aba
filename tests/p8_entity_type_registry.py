"""Phase 4.2 (misc/phase4_entity_types.md): entity-type registry contract.

Five invariants:

1. Every bio entity-type YAML loads successfully.
2. The set of registered type names matches today's set of types used
   in create_entity calls (figure, table, dataset, analysis, result,
   claim, finding, narrative, thread, plan, note, capability,
   reference, workspace).
3. Hidden types (capability, reference) report hidden=True.
4. Status-transition predicates work for known transitions and reject
   undeclared ones.
5. Edge predicates work — a figure can `supports` a claim, but a
   workspace cannot `supports` anything.

Deterministic. No DB, no server.

Run:
    .venv/bin/python tests/p8_entity_type_registry.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Importing bio triggers load_types(). Stays platform-impure on the test
# side — this test is bio-tier (it asserts about bio's types).
import content.bio  # noqa: E402, F401
from core.entity_types import (  # noqa: E402
    get_type, list_type_names, valid_status_transition, valid_edge,
    is_hidden, hidden_types, card_builder_ref, ui_panel_ref,
)


EXPECTED = {
    "figure", "table", "dataset", "analysis", "result", "claim",
    "finding", "narrative", "thread", "plan", "note",
    "capability", "reference", "workspace",
}


def test_all_14_types_load():
    names = set(list_type_names())
    missing = EXPECTED - names
    extra = names - EXPECTED
    assert not missing and not extra, f"missing={missing}  extra={extra}"


def test_hidden_types_match_hardcoded_set():
    # entities.py's HIDDEN_TYPES is the source of truth today.
    from core.graph.entities import HIDDEN_TYPES
    assert set(hidden_types()) == set(HIDDEN_TYPES), \
        f"YAMLs hide {set(hidden_types())} but code hides {set(HIDDEN_TYPES)}"
    assert is_hidden("capability") and is_hidden("reference")
    assert not is_hidden("figure")


def test_status_transitions_known_paths():
    # Figure: active → superseded → archived is allowed; active → done is not.
    assert valid_status_transition("figure", "active", "superseded")
    assert valid_status_transition("figure", "superseded", "archived")
    assert not valid_status_transition("figure", "active", "done")
    # Analysis: a Run can transition active → running → completed.
    assert valid_status_transition("analysis", "active", "running")
    assert valid_status_transition("analysis", "running", "completed")
    assert valid_status_transition("analysis", "running", "cancelled")
    assert not valid_status_transition("analysis", "completed", "running")
    # Workspace: singleton; no transitions allowed (status states=['active']).
    assert not valid_status_transition("workspace", "active", "archived")


def test_edges_known_relationships():
    # Figure → supports → claim
    assert valid_edge("figure", "claim", "supports")
    # Result → supports → claim
    assert valid_edge("result", "claim", "supports")
    # Workspace cannot supports anything (out: [])
    assert not valid_edge("workspace", "claim", "supports")
    # Analysis → used → dataset (PROV-O)
    assert valid_edge("analysis", "dataset", "used")
    # Bogus relationship
    assert not valid_edge("figure", "claim", "is_better_than")


def test_card_builder_refs_resolve_to_real_imports():
    """If a YAML declares a card_builder, it must resolve to a real
    callable. Omitting card_builder is fine — the assembler falls back
    to `_generic_card` for types without a registered builder (today
    that's everything except analysis and plan). Catches drift between
    YAML and code at startup."""
    import importlib
    declared = 0
    for name in list_type_names():
        ref = card_builder_ref(name)
        if not ref:
            continue
        declared += 1
        try:
            module, attr = ref.rsplit(".", 1)
            mod = importlib.import_module(module)
            fn = getattr(mod, attr)
            assert callable(fn), f"{name}: {ref} is not callable"
        except (ImportError, AttributeError, AssertionError) as exc:
            raise AssertionError(f"{name}: card_builder '{ref}' unresolvable: {exc}")
    # Today: only analysis + plan declare custom builders.
    assert declared == 2, f"expected 2 custom card_builders, found {declared}"


def test_ui_panel_refs_are_strings():
    """Forward references — actual frontend dispatch happens in Phase 4.6.
    For now, just assert each type has a panel string."""
    for name in list_type_names():
        panel = ui_panel_ref(name)
        assert panel, f"{name}: no ui.panel declared"
        assert isinstance(panel, str)


def main() -> int:
    tests = [
        test_all_14_types_load,
        test_hidden_types_match_hardcoded_set,
        test_status_transitions_known_paths,
        test_edges_known_relationships,
        test_card_builder_refs_resolve_to_real_imports,
        test_ui_panel_refs_are_strings,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
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
