"""Where the substrate checkout is — ONE owner, because two guesses drifted.

Two cross-repo guards need a weft checkout: the envelope drift test and the
error-code lever test. Each grew its own list of candidate paths, and the first
one shipped with a single hard-coded guess (`~/aba/weft`) that matched nothing
on the box it was written on — so it skipped silently while 72 substrate
commits landed, including a contract change it existed to catch.

Order matters and is deliberate: `aba-vbc/work/weft` FIRST, because that is the
checkout `build.sh` bakes into the release. A guard should measure the
substrate that SHIPS, not whichever copy a developer happens to keep.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def weft_candidates() -> "list[Path]":
    """Candidate checkout ROOTS, most authoritative first."""
    out: list[Path] = []
    env = (os.environ.get("ABA_WEFT_SRC") or "").strip()
    if env:
        out.append(Path(env).expanduser())
    home = Path.home()
    out += [
        REPO.parent / "aba-vbc" / "work" / "weft",   # the one that gets baked
        home / "aba" / "weft",
        home / "weft",
        home / "weft-src" / "weft",
        REPO.parent / "weft",
        Path("/scratch/users") / home.name / "aba-weft" / "repo" / "weft",
    ]
    seen, uniq = set(), []
    for c in out:
        if str(c) not in seen:
            seen.add(str(c))
            uniq.append(c)
    return uniq


def find_weft_file(*rel: str) -> "Path | None":
    """The first existing checkout that has this file, or None."""
    return next((p for c in weft_candidates()
                 if (p := c.joinpath(*rel)).exists()), None)


def tried(*rel: str) -> str:
    """Human-readable 'looked here' for a skip message. A bare 'not found'
    reads as 'nothing to do'; naming the paths makes a WRONG guess visible."""
    return ", ".join(str(c.joinpath(*rel)) for c in weft_candidates())
