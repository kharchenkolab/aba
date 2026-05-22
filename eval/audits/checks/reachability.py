"""Tier-1 reachability audit.

Every primary action a state declares must exist, be visible, have non-zero
size, and sit within the viewport (or be vertically scrollable to — flagged
only if it needs horizontal scrolling, which a user shouldn't have to do).
Also flags page-level horizontal overflow once per state.
"""
from __future__ import annotations

NAME = "reachability"

_ACTION_EVAL = """
(sel) => {
  const els = [...document.querySelectorAll(sel)];
  if (!els.length) return [{selector: sel, issue: 'missing'}];
  const out = [];
  const vw = innerWidth, vh = innerHeight;
  // Any visible, non-zero instance reachable without horizontal scroll passes.
  let ok = false;
  for (const el of els) {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || cs.visibility === 'hidden' || cs.display === 'none') continue;
    const needsHScroll = r.left < -1 || r.right > vw + 1;
    if (!needsHScroll) { ok = true; break; }
  }
  if (!ok) out.push({selector: sel, issue: 'not-reachable'});
  return out;
}
"""


def run(page, state) -> list[dict]:
    out: list[dict] = []
    for sel in state.primary_actions:
        out.extend(page.evaluate(_ACTION_EVAL, sel))
    if page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"):
        out.append({"selector": "<page>", "issue": "horizontal-scroll"})
    return out
