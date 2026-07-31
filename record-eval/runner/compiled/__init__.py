"""Compiled corpus assertions — predicate calls keyed by (pool, scenario, index).

The scenario JSONs stay authoritative and untouched; each CompiledAssertion is
one predicate call with window parameters, compiled by hand from the corpus
assertion's English sentence.  `reading_note` is non-empty wherever the
compilation is an interpretation (ambiguous sentence, corpus tension, or a
clause not observable from the trace) — these notes feed RUNNER_REPORT.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CompiledAssertion:
    pool: str
    scenario: str
    index: int
    kind: str
    predicate: str
    window: tuple | None   # (t_lo, t_hi) or None for END
    params: dict = field(default_factory=dict)
    reading_note: str = ""


def _mk(pool, scenario, entries):
    out = []
    for i, (kind, predicate, window, params, *rest) in enumerate(entries):
        out.append(CompiledAssertion(
            pool=pool, scenario=scenario, index=i, kind=kind,
            predicate=predicate, window=window, params=params,
            reading_note=rest[0] if rest else ""))
    return out


def registry():
    from . import checkout, gaia_bh1
    reg = {}
    for mod in (checkout, gaia_bh1):
        for ca in mod.COMPILED:
            reg.setdefault((ca.pool, ca.scenario), []).append(ca)
    for k in reg:
        reg[k].sort(key=lambda c: c.index)
    return reg
