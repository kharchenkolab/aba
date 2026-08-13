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


def _cli(argv=None) -> int:
    """`python fixtures.py --complete <scenario-dir>` → 0 complete, 1 incomplete.

    ONE reader of `data_files:`. _regen_all.sh used to parse the declaration
    itself in awk and got it wrong twice — dropping every entry after the first
    (a `sub()` that mutated the record the terminator rule then matched), and
    never seeing flow style (`data_files: [x.csv]`) at all, which silently fell
    back to a bare "data/ is non-empty" test. A generator that skips on an
    incomplete data/ produces a scenario the sweep cannot run.

    Exit 2 = could not determine (no YAML): the caller should fall back rather
    than treat "unknown" as either answer."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2 or argv[0] != "--complete":
        print("usage: fixtures.py --complete <scenario-dir>", file=sys.stderr)
        return 2
    try:
        import yaml
    except ImportError:
        return 2
    d = Path(argv[1])
    try:
        spec = yaml.safe_load((d / "scenario.yaml").read_text()) or {}
    except Exception:  # noqa: BLE001
        return 2
    declared = declared_inputs(spec)
    data = d / "data"
    if not declared:                       # nothing declared → the old test
        return 0 if data.is_dir() and any(data.iterdir()) else 1
    return 1 if missing_inputs(declared, data) else 0


if __name__ == "__main__":
    raise SystemExit(_cli())
