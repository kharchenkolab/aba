"""UI wire-contract conformance (weft rewrite W0.1 — misc/weft_rewrite.md §6.7).

Three guarantees:
  1. The registry's builders are sound (required/unknown-field validation works).
  2. `frontend/src/wire.ts` is in sync with `backend/core/runtime/wire.py`
     (the generated TS is the frontend's ONLY source for event shapes).
  3. No producer bypasses the contract: event payloads handed to the transports
     (`sse(...)` in guide.py, `sink.push(...)`, `broadcast(...)`) and the
     `_emit_sse_*` envelope keys must be built via `wire.*`, never as dict
     literals. This is what keeps types.ts ↔ backend drift structurally
     impossible instead of merely fixed-once.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(REPO / "scripts"))


# ── 1. builder soundness ─────────────────────────────────────────────────────

def test_every_event_has_a_working_builder():
    from core.runtime import wire
    for name, spec in wire.EVENTS.items():
        builder = getattr(wire, name)
        fields = {f: "x" for f in spec.required}
        payload = builder(**fields)
        assert payload["type"] == name
        for f in spec.required:
            assert f in payload


def test_builder_rejects_missing_and_unknown_fields():
    from core.runtime import wire
    with pytest.raises(TypeError, match="missing required"):
        wire.delta()
    with pytest.raises(TypeError, match="unknown fields"):
        wire.delta(text="hi", nope=1)
    with pytest.raises(AttributeError):
        wire.no_such_event


def test_transport_check_warns_but_never_raises(capsys):
    from core.runtime import wire
    wire._warned.clear()
    wire.check({"type": "not_an_event"}, "turn")          # unknown type
    wire.check({"type": "delta"}, "turn")                 # missing required
    wire.check({"type": "entity_updated", "entity_id": "e", "reason": "r"},
               "turn")                                    # wrong channel
    wire.check("not a dict", "turn")                      # non-dict: ignored
    out = capsys.readouterr().out
    assert out.count("[wire] non-conformant") == 3
    # warn-once: repeats are silent
    wire.check({"type": "not_an_event"}, "turn")
    assert capsys.readouterr().out == ""
    wire._warned.clear()


def test_valid_payloads_pass_check_silently(capsys):
    from core.runtime import wire
    wire.check(wire.done(), "turn")
    wire.check(wire.usage(input=1, output=2, cache_read=0, cache_write=0), "turn")
    wire.check({**wire.delta(text="hi"), "seq": 7}, "turn")   # framing seq is fine
    wire.check(wire.entity_updated(entity_id="e", reason="r"), "notify")
    assert "[wire]" not in capsys.readouterr().out


# ── 2. generated TS in sync ──────────────────────────────────────────────────

def test_wire_ts_in_sync_with_registry():
    import gen_wire_types
    generated = gen_wire_types.render()
    committed = (REPO / "frontend" / "src" / "wire.ts").read_text()
    assert committed == generated, (
        "frontend/src/wire.ts is out of sync with backend/core/runtime/wire.py — "
        "run: python scripts/gen_wire_types.py")


def test_resource_imports_exist_in_types_ts():
    """Every hand-maintained interior wire.ts references must actually be
    exported by types.ts (a rename there would silently break the contract)."""
    from core.runtime import wire
    types_ts = (REPO / "frontend" / "src" / "types.ts").read_text()
    for t in wire.RESOURCE_IMPORTS:
        assert (f"export interface {t}" in types_ts
                or f"export type {t}" in types_ts), \
            f"wire contract references TS type {t!r} but types.ts does not export it"


# ── 3. no producer bypasses the contract ─────────────────────────────────────

_TRANSPORT_CALLS = {"sse", "push", "broadcast"}


def _dict_literal_offenders(py: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(py.read_text(errors="replace"))
    except SyntaxError:
        return []
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname in _TRANSPORT_CALLS and node.args \
                    and isinstance(node.args[0], ast.Dict):
                d = node.args[0]
                keys = {k.value for k in d.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                if "type" in keys:
                    hits.append((node.lineno, f"{fname}({{'type': ...}})"))
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and isinstance(k.value, str)
                        and k.value.startswith("_emit_sse")
                        and isinstance(v, ast.Dict)):
                    hits.append((node.lineno, f"{k.value}: {{...}} literal"))
    return hits


def test_no_event_dict_literals_at_transport_callsites():
    offenders = {}
    for py in sorted(BACKEND.rglob("*.py")):
        rel = str(py.relative_to(BACKEND))
        if rel == "core/runtime/wire.py" or "/tests/" in rel:
            continue
        hits = _dict_literal_offenders(py)
        if hits:
            offenders[rel] = hits
    assert not offenders, (
        "Event payloads must be built via core.runtime.wire builders, "
        "never dict literals at the transport callsite. Offenders:\n"
        + "\n".join(f"  {f}: {hits}" for f, hits in offenders.items()))
