#!/usr/bin/env python3
"""Generate frontend/src/wire.ts from the backend wire-contract registry.

Usage:  python scripts/gen_wire_types.py [--check]

--check: exit 1 if the committed wire.ts differs from what the registry
produces (the conformance test runs this in-process).

The generated file is the frontend's ONLY source for SSE event shapes;
frontend/src/types.ts re-exports from it. Resource interiors (Entity, JobInfo,
ManifestSnapshot) remain hand-maintained in types.ts and are imported here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

OUT = REPO / "frontend" / "src" / "wire.ts"


def _iface_name(event: str) -> str:
    return "".join(p.capitalize() for p in event.split("_")) + "Event"


def render() -> str:
    from core.runtime import wire

    s = wire.schema()
    lines: list[str] = []
    w = lines.append
    w("// GENERATED FILE — do not edit.")
    w("// Source of truth: backend/core/runtime/wire.py")
    w("// Regenerate:      python scripts/gen_wire_types.py")
    w("// Sync-guarded by  tests/test_wire_contract.py")
    w("")
    if s["resource_imports"]:
        w(f"import type {{ {', '.join(s['resource_imports'])} }} from './types';")
        w("")
    w("/** Wire framing adds a monotonic seq to every turn-channel event. */")
    w("export interface TurnEventBase {")
    w("  seq?: number;")
    w("}")
    for aux, fields in s["aux_types"].items():
        w("")
        w(f"export interface {aux} {{")
        for f, t in fields.items():
            w(f"  {f}: {t};")
        w("}")
    unions: dict[str, list[str]] = {"turn": [], "notify": []}
    for name, spec in s["events"].items():
        iface = _iface_name(name)
        unions[spec["channel"]].append(iface)
        w("")
        w(f"/** {spec['doc']} */")
        base = " extends TurnEventBase" if spec["channel"] == "turn" else ""
        w(f"export interface {iface}{base} {{")
        w(f"  type: '{name}';")
        for f, t in spec["required"].items():
            w(f"  {f}: {t};")
        for f, t in spec["optional"].items():
            w(f"  {f}?: {t};")
        w("}")
    w("")
    w("/** Every event the per-turn chat stream can carry. */")
    w("export type SSEEvent =")
    for i, iface in enumerate(unions["turn"]):
        w(f"  | {iface}" + (";" if i == len(unions["turn"]) - 1 else ""))
    w("")
    w("/** Every event the global /api/notifications stream can carry. */")
    w("export type NotificationEvent =")
    for i, iface in enumerate(unions["notify"]):
        w(f"  | {iface}" + (";" if i == len(unions["notify"]) - 1 else ""))
    w("")
    return "\n".join(lines)


def main() -> int:
    text = render()
    if "--check" in sys.argv:
        current = OUT.read_text() if OUT.exists() else ""
        if current != text:
            print(f"OUT OF SYNC: {OUT} does not match backend/core/runtime/wire.py; "
                  "run: python scripts/gen_wire_types.py")
            return 1
        print("wire.ts in sync")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
