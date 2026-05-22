"""Tier-1 audit checks. Each module exposes NAME and run(page, state) -> list[dict]."""
from . import contrast, clipping, reachability, tap_target

ALL = [contrast, clipping, reachability, tap_target]
