"""Tier-1 tap-target audit.

Flags visible interactive elements whose rendered box is too small to hit
comfortably. Scoped to audit_root when a state sets it. Findings are advisory
(many icon buttons sit near the line); the baseline absorbs accepted ones.
"""
from __future__ import annotations

NAME = "tap_target"

MIN = 24      # comfortable minimum (px)
FAIL = 18     # clearly too small

_EVAL = """
([rootSel, minPx]) => {
  const root = rootSel ? document.querySelector(rootSel) : document;
  if (!root) return [];
  const els = [...root.querySelectorAll('button, a[href], [role=button]')];
  const out = [];
  const vw = innerWidth, vh = innerHeight;
  for (const el of els) {
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.pointerEvents === 'none') continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;                 // not rendered
    if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;  // off-screen
    const m = Math.min(r.width, r.height);
    if (m < minPx) {
      out.push({
        selector: (el.className || el.tagName).toString().slice(0, 60),
        w: Math.round(r.width), h: Math.round(r.height),
        title: (el.getAttribute('title') || el.textContent || '').trim().slice(0, 30),
      });
    }
  }
  return out;
}
"""


def run(page, state) -> list[dict]:
    rows = page.evaluate(_EVAL, [state.audit_root, MIN])
    for r in rows:
        r["severity"] = "fail" if min(r["w"], r["h"]) < FAIL else "warn"
    return rows
