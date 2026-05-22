"""Tier-1 clipping / occlusion audit.

For each selector a state declares in `must_show` (open menus, modals, popovers,
key buttons), verify it is present, non-zero, fully inside the viewport, and not
covered by another element at its center point.
"""
from __future__ import annotations

NAME = "clipping"

_EVAL = """
(sel) => {
  const els = [...document.querySelectorAll(sel)];
  if (!els.length) return [{selector: sel, issue: 'absent'}];
  const out = [];
  const vw = innerWidth, vh = innerHeight;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) { out.push({selector: sel, issue: 'zero-size'}); continue; }
    if (r.right > vw + 1 || r.bottom > vh + 1 || r.left < -1 || r.top < -1) {
      out.push({selector: sel, issue: 'off-viewport',
                rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}});
    }
    const cx = Math.min(vw - 1, Math.max(0, r.left + r.width / 2));
    const cy = Math.min(vh - 1, Math.max(0, r.top + r.height / 2));
    const top = document.elementFromPoint(cx, cy);
    if (top && !el.contains(top) && !top.contains(el)) {
      out.push({selector: sel, issue: 'occluded', by: (top.className || top.tagName).toString().slice(0, 60)});
    }
  }
  return out;
}
"""


def run(page, state) -> list[dict]:
    out: list[dict] = []
    for sel in state.must_show:
        out.extend(page.evaluate(_EVAL, sel))
    return out
