"""ONE definition of "are this scenario's declared inputs actually present?".

Two callers need this answer and they must never disagree:

  * `sweep.preflight_fixtures` asks it STATICALLY, against the scenario's
    `data/` tree, before a run starts — milliseconds, no boot;
  * `runner.py` asks it after staging, against the project's DATA_DIR — the
    authoritative check, but only reachable after a full app boot, and inside a
    sweep that verdict lands hours in.

When these drifted apart they were wrong in both directions at once: the sweep
skipped scenarios the runner would have run, and the runner killed scenarios
whose inputs were staged perfectly well. The second cost real coverage — a
declaration may carry a subdirectory ("sub/in.csv"), staging copies that subdir
in wholesale, and a TOP-LEVEL listing then sees only "sub" and calls all eight
nested inputs missing. Both shapes are first-class; both live here now.
"""
from __future__ import annotations

from pathlib import Path


def declared_inputs(spec: dict) -> list[str]:
    """The scenario's declared inputs, as written (subdirectory kept).

    Accepts both spellings a scenario.yaml may use: a bare string, or a mapping
    with `name`/`path`."""
    out = []
    for d in (spec.get("data_files") or []):
        v = d if isinstance(d, str) else (d.get("name") or d.get("path") or "")
        if v:
            out.append(v)
    return out


def present_names(root: Path) -> set[str]:
    """Every file under `root`, indexed by BOTH its basename and its path
    relative to root — recursively, so nested staging resolves."""
    names: set[str] = set()
    if not root or not root.is_dir():
        return names
    for p in root.rglob("*"):
        if p.is_file():
            names.add(p.name)
            names.add(str(p.relative_to(root)))
    return names


def missing_inputs(declared, root: Path) -> list[str]:
    """Which declared inputs are absent under `root`. A declaration matches on
    its relative path OR its basename — staging may flatten or preserve the
    subdirectory, and either is a correctly-provisioned fixture."""
    present = present_names(root)
    return [d for d in declared
            if d not in present and Path(d).name not in present]
