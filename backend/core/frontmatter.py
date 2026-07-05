"""Canonical frontmatter parser (burn-down #4).

One implementation of the `--- YAML --- body` markdown convention, replacing the
near-identical `_split_frontmatter` copies that had accreted across the skills,
memory, and bundle loaders. Frontmatter is a shared agent input (`search_skills`
indexes frontmatter, not bodies), so a single parser prevents the loaders from
drifting on edge cases (unterminated blocks, non-mapping YAML, body stripping).
"""
from __future__ import annotations

import yaml

_SPLIT = "---"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading `--- … ---` YAML block off a markdown file and return
    (frontmatter_dict, body).

    - No frontmatter → ({}, text.strip()).
    - Malformed (unterminated fence, invalid YAML, or a non-mapping top level)
      → ValueError, so a typo in a checked-in file fails loudly at load time.
    """
    if not text.startswith(_SPLIT):
        return {}, text.strip()
    # Closing fence on its own line.
    rest = text[len(_SPLIT):]
    end_idx = rest.find("\n" + _SPLIT)
    if end_idx == -1:
        raise ValueError("unterminated frontmatter block")
    fm_raw = rest[:end_idx]
    body = rest[end_idx + len("\n" + _SPLIT):].lstrip("\n").strip()
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"frontmatter YAML parse error: {e}") from e
    if not isinstance(fm, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return fm, body
