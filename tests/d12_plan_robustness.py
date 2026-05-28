"""
present_plan robustness — a live run produced "⛔ The plan has no steps" plus
leaked function-call XML ('</assumptions>', '<parameter name="steps">') in the
plan card, even though the model had emitted 4 steps. The steps had been crammed
(as a JSON array / leaked XML) into a text field instead of the `steps` param.

normalize_plan must tolerate that: parse steps given as a JSON string, recover
steps embedded in another field, and strip leaked tags + embedded JSON from the
displayed prose. Deterministic (no model). Run:
    .venv/bin/python tests/d12_plan_robustness.py
"""
from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("ABA_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="aba_d12_"), "t.db"))

from core.planning.validator import normalize_plan, validate_plan  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def has_no_steps_concern(plan) -> bool:
    return any("no steps" in (c.message or "").lower() for c in (plan.concerns or []))


def main() -> int:
    print("plan step parsing + leak stripping")

    # 1. canonical list — unchanged
    p = validate_plan(normalize_plan({"title": "T", "steps": [{"title": "QC"}, {"title": "Cluster"}]}))
    check("canonical list parsed", [s.title for s in p.steps] == ["QC", "Cluster"])
    check("no false 'no steps' concern", not has_no_steps_concern(p))

    # 2. steps passed as a JSON STRING
    p = normalize_plan({"title": "T", "steps": '[{"title":"Load"},{"title":"Run"}]'})
    check("JSON-string steps parsed", [s.title for s in p.steps] == ["Load", "Run"])

    # 3. steps crammed (leaked function-call XML) into `assumptions`, steps empty
    p = validate_plan(normalize_plan({
        "title": "Pagoda2 plan", "summary": "process two samples",
        "assumptions": 'Default settings</assumptions><parameter name="steps">'
                       '[{"title":"Load h5ad","description":"read files"},{"title":"Run pagoda2"}]',
    }))
    check("embedded steps recovered", [s.title for s in p.steps] == ["Load h5ad", "Run pagoda2"], str([s.title for s in p.steps]))
    check("no 'no steps' concern after recovery", not has_no_steps_concern(p))
    check("leaked tags stripped from assumptions",
          all("<" not in a and "parameter name" not in a for a in p.assumptions), str(p.assumptions))
    check("embedded JSON stripped from assumptions",
          all("{" not in a for a in p.assumptions), str(p.assumptions))

    # 4. genuinely empty plan still flags 'no steps' (the concern is real then)
    p = validate_plan(normalize_plan({"title": "T", "summary": "nothing here"}))
    check("truly empty plan → 'no steps' concern", has_no_steps_concern(p))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures)); return 1
    print("ALL PLAN-ROBUSTNESS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
