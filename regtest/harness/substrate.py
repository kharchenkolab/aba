#!/usr/bin/env python
"""Which substrate did this measurement run against — and is that answer unique?

A regtest verdict is a statement about a PAIRING: the aba tree under test and
the weft substrate it calls. The harness records the first half (sweep stamps
the git sha) and never the second. When the two drift a red result is
ambiguous — "the product regressed" and "your substrate is four weeks old" look
identical — and on 2026-08-13 that ambiguity produced three false failures in
one session (env_capability_promoted, r_system_library, and the viewer parity
oracle) plus one wrong diagnosis handed to another engineer.

The measurable defect underneath is not staleness, it is AMBIGUITY. Two weft
copies were importable from the harness interpreter:

    sys.path[4]  ~/.local/lib/python3.12/site-packages/weft    4c8b647f  53 files
    sys.path[5]  ~/.aba/env/lib/python3.12/site-packages/weft  d42d4a0d  50 files

`site.ENABLE_USER_SITE` is True in that venv, so user site precedes it and the
FRESHER copy won by accident. Set PYTHONNOUSERSITE=1 — as any `--containall`
run does — and the identical tree measures a four-week-old substrate, with
nothing in the output naming which one answered.

So this module does two things and refuses to guess at a third:

  * `check_substrate()` REFUSES when a substrate package resolves to more than
    one distinct content hash. Which copy wins is then a property of the
    interpreter's flags rather than of the deployment, and no verdict computed
    that way can be attributed. Duplicates OUTSIDE the named substrate set are
    reported, not refused — a blanket rule would trip on the namespace and
    vendored packages every large env carries.
  * `stamp()` records the identity that DID answer, so a verdict can be
    attributed months later. Byte-identical copies in two places are untidy but
    not a measurement hazard; they are reported, never refused.

It deliberately does NOT check "is weft current". The harness has no authority
on what current is, and a version floor here would be a second, lying reader of
a fact the deployment owns.

    python regtest/harness/substrate.py          # print the stamp; exit 1 if ambiguous
"""
from __future__ import annotations

import hashlib
import os
import sys
from importlib.machinery import PathFinder
from pathlib import Path

# The packages whose identity decides what a red result MEANS. Only these are
# refusal-worthy; see the module docstring on why the rule is not blanket.
SUBSTRATE_PACKAGES = ("weft",)


def _content_hash(loc: Path) -> tuple[str, int]:
    """Hash a package dir (or single-module file) by name+bytes of its sources.

    Sources only: .pyc mtimes and RECORD metadata differ between two installs
    of the same code, and would report every deployment as a distinct
    substrate."""
    if loc.is_file():
        return hashlib.sha256(loc.read_bytes()).hexdigest()[:12], 1
    h = hashlib.sha256()
    n = 0
    for f in sorted(loc.rglob("*.py")):
        h.update(f.relative_to(loc).as_posix().encode())
        h.update(f.read_bytes())
        n += 1
    return h.hexdigest()[:12], n


def copies(name: str, search_path: list[str] | None = None) -> list[dict]:
    """Every importable `name` on the search path, in RESOLUTION order.

    Uses PathFinder per entry rather than globbing for `<entry>/<name>`, so the
    answer matches what `import name` would actually bind — including
    single-file modules and eggs, and excluding a directory that merely shares
    the name without being importable."""
    out: list[dict] = []
    seen: set[str] = set()
    for entry in (sys.path if search_path is None else search_path):
        real = os.path.realpath(entry or os.getcwd())
        if real in seen:
            continue
        seen.add(real)
        try:
            spec = PathFinder.find_spec(name, [entry or os.getcwd()])
        except (ImportError, ValueError, OSError):
            continue
        if spec is None:
            continue
        if spec.origin in (None, "namespace"):
            # A namespace portion has no single content to hash; record it so
            # it is visible, but it can never be the thing that answers alone.
            out.append({"path": real, "origin": "namespace", "hash": None, "files": 0})
            continue
        origin = Path(spec.origin)
        loc = origin.parent if origin.name == "__init__.py" else origin
        try:
            h, n = _content_hash(loc)
        except OSError as e:  # unreadable install — name it, do not swallow
            out.append({"path": str(loc), "origin": "unreadable",
                        "hash": None, "files": 0, "error": str(e)})
            continue
        out.append({"path": str(loc), "origin": str(origin), "hash": h, "files": n})
    return out


def identity(names=SUBSTRATE_PACKAGES, search_path=None) -> dict[str, list[dict]]:
    return {n: copies(n, search_path) for n in names}


def _distinct(cs: list[dict]) -> set[str]:
    return {c["hash"] for c in cs if c["hash"]}


def check_substrate(names=SUBSTRATE_PACKAGES, search_path=None) -> list[str]:
    """Problems that make a measurement unattributable. Empty == safe to measure."""
    problems = []
    for name, cs in identity(names, search_path).items():
        if len(_distinct(cs)) > 1:
            where = "\n".join(
                f"              {i}. {c['hash'] or c['origin']:>12}  "
                f"{c['files']:>3} files  {c['path']}"
                for i, c in enumerate(cs, 1))
            problems.append(
                f"`{name}` resolves to {len(_distinct(cs))} DIFFERENT copies on this "
                f"interpreter's path — the first wins, and which one that is depends "
                f"on interpreter flags (PYTHONNOUSERSITE / site.ENABLE_USER_SITE), "
                f"not on the deployment. Any verdict here is unattributable.\n"
                f"{where}\n"
                f"              Fix: bring them into agreement (reinstall the venv's "
                f"copy) or remove the shadowing one.")
    return problems


def stamp(names=SUBSTRATE_PACKAGES, search_path=None) -> str:
    """One line naming the substrate that ANSWERED, for the run record."""
    bits = []
    for name, cs in identity(names, search_path).items():
        if not cs:
            bits.append(f"{name}=absent")
            continue
        first = cs[0]
        extra = f" (+{len(cs) - 1} shadowed)" if len(cs) > 1 else ""
        bits.append(f"{name}={first['hash'] or first['origin']}"
                    f"@{first['path']}{extra}")
    return "  ".join(bits)


def main() -> int:
    problems = check_substrate()
    print(f"[substrate] {stamp()}")
    print(f"[substrate] user-site enabled: "
          f"{not sys.flags.no_user_site}   interpreter: {sys.executable}")
    for p in problems:
        print(f"[substrate] AMBIGUOUS: {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
