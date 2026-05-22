"""Audit report aggregation + regression gate.

A run produces findings keyed by (state, check). We reduce each finding to a
stable *signature* and compare the set against an accepted baseline:

  - findings whose signature is in the baseline are accepted (no-op);
  - findings not in the baseline are REGRESSIONS → non-zero exit;
  - baseline signatures absent from the run are reported as "fixed".

First run (or --update-baseline) writes the baseline from the current findings.
Counts are intentionally collapsed per signature: we gate on *new kinds* of
defects, not exact multiplicities.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

AUDIT_DIR = Path(__file__).resolve().parent
BASELINE = AUDIT_DIR / "baseline.json"
RUNS = AUDIT_DIR.parent / "runs" / "audits"


def _key(check: str, f: dict) -> str:
    if check == "contrast":
        return f.get("selector", "?")
    if check in ("clipping", "reachability"):
        return f"{f.get('selector', '?')}::{f.get('issue', '?')}"
    if check == "tap_target":
        return f"{f.get('selector', '?')}::{f.get('severity', '?')}"
    return json.dumps(f, sort_keys=True)


def signatures(report: dict) -> set[tuple[str, str, str]]:
    sigs = set()
    for state, checks in report.items():
        for check, findings in checks.items():
            if check.startswith("_"):
                continue
            for f in findings:
                sigs.add((state, check, _key(check, f)))
    return sigs


def load_baseline() -> set[tuple[str, str, str]]:
    if not BASELINE.exists():
        return set()
    raw = json.loads(BASELINE.read_text())
    return {tuple(s) for s in raw.get("signatures", [])}


def write_baseline(report: dict) -> int:
    sigs = sorted(signatures(report))
    BASELINE.write_text(json.dumps(
        {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "signatures": [list(s) for s in sigs]}, indent=2))
    return len(sigs)


def write_run(report: dict, shots: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = RUNS / ts
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2))
    return out


def gate(report: dict) -> tuple[set, set]:
    """Return (regressions, fixed) signature sets vs the baseline."""
    cur = signatures(report)
    base = load_baseline()
    return cur - base, base - cur
