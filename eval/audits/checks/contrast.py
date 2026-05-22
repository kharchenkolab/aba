"""Tier-1 contrast audit — WCAG color-contrast via vendored axe-core.

No LLM, no network: injects the bundled axe.min.js and runs only the
`color-contrast` rule, returning one finding per offending node.
"""
from __future__ import annotations
from pathlib import Path

AXE = Path(__file__).resolve().parents[1] / "vendor" / "axe.min.js"

NAME = "contrast"

_EVAL = """
async (sel) => {
  const ctx = sel ? document.querySelector(sel) : document;
  if (!ctx) return [];
  const res = await axe.run(ctx, { runOnly: ['color-contrast'] });
  const out = [];
  for (const v of res.violations) {
    for (const n of v.nodes) {
      const d = (n.any && n.any[0] && n.any[0].data) || {};
      out.push({
        selector: (n.target || []).join(' '),
        impact: n.impact,
        fg: d.fgColor, bg: d.bgColor,
        ratio: d.contrastRatio, expected: d.expectedContrastRatio,
      });
    }
  }
  return out;
}
"""


def run(page, state) -> list[dict]:
    if not page.evaluate("() => !!window.axe"):
        page.add_script_tag(path=str(AXE))
    return page.evaluate(_EVAL, state.audit_root)
