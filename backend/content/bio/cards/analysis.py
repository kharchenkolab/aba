"""Focus card for analysis-run entities.

Adds run status / executor / command / outputs / bulk file count to the
generic card, so the Guide knows what the user is looking at when they
ask about a specific run output.
"""
from __future__ import annotations

from core.manifest.assembler import _generic_card, register_card_builder


def build_analysis_card(entity: dict) -> tuple[str, list[str]]:
    text, fields = _generic_card(entity)
    run = (entity.get("metadata") or {}).get("run") or {}
    extras = []
    if run.get("status"):
        bits = [f"status {run['status']}"]
        if run.get("executor"):
            bits.append(f"executor {run['executor']}" + (f" on {run['where']}" if run.get("where") else ""))
        extras.append("This is an analysis run — " + ", ".join(bits) + ".")
        fields.append("run_meta")
    if run.get("command"):
        cmd = run["command"].strip()
        extras.append("Command: " + (cmd[:300] + " …" if len(cmd) > 300 else cmd))
        fields.append("run_command")
    labels = [o.get("label", "") for o in (run.get("outputs") or []) if o.get("label")]
    if labels:
        extras.append("Outputs produced by this run: " + ", ".join(labels[:30]) + ".")
        fields.append("run_outputs")
    if (run.get("bulk") or {}).get("count"):
        extras.append(f"(plus {run['bulk']['count']} bulk files, not individually listed).")
    if extras:
        text = text + "\n" + "\n".join(extras)
    return text, fields


register_card_builder("analysis", build_analysis_card)
