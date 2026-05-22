"""The driver loop: render → policy picks one action → execute → re-render →
log, until the scientist calls `done` or the step budget is hit.

Logs every step plus a final entity snapshot, and derives the first effort
metric (actions-to-milestone). Stage 4 will score these logs against the
scenario's planted ground truth.
"""
from __future__ import annotations
import json
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import view as view_mod
import actions as actions_mod

# action → milestone it first achieves
MILESTONE_ACTION = {"pin": "first_kept", "promote_figure": "first_result",
                    "save_finding": "first_finding"}


def _get(api, path):
    with urllib.request.urlopen(f"{api}{path}", timeout=30) as r:
        return json.loads(r.read())


def run_scenario(api: str, policy, *, focus_start: str = "workspace",
                 budget: int = 15) -> dict:
    ctx = SimpleNamespace(api=api, focus_id=focus_start, last_search=None, done=False)
    log: list[dict] = []
    milestones: dict[str, int] = {}
    guide_usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    last_obs: str | None = None

    for step in range(budget):
        view = view_mod.render(api, ctx.focus_id, ctx.last_search)
        ctx.last_search = None                       # search results shown once
        name, inp = policy.act(view, last_obs)
        res = actions_mod.execute(name, inp, ctx)
        last_obs = res.get("observation", "")
        if res.get("guide_usage"):
            for k in guide_usage:
                guide_usage[k] += res["guide_usage"].get(k, 0)
        if name in MILESTONE_ACTION and MILESTONE_ACTION[name] not in milestones:
            milestones[MILESTONE_ACTION[name]] = step
        log.append({"step": step, "focus": ctx.focus_id, "action": name,
                    "input": inp, "observation": last_obs})
        print(f"  [{step}] {name}({_brief(inp)}) → {last_obs[:90]}")
        if ctx.done or name == "done":
            break

    z = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    scientist_usage = getattr(policy, "usage", z)

    def _tot(u):
        return u["input"] + u["output"] + u.get("cache_read", 0) + u.get("cache_write", 0)
    tokens = {
        "scientist": scientist_usage,
        "guide": guide_usage,
        "total": _tot(scientist_usage) + _tot(guide_usage),
    }
    entities = _get(api, "/entities")
    return {"steps": len(log), "log": log, "milestones": milestones,
            "tokens": tokens, "entities": entities}


def _brief(inp: dict) -> str:
    return ", ".join(f"{k}={str(v)[:40]}" for k, v in inp.items())


def write_run(result: dict, scenario_id: str, base: Path) -> Path:
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = base / scenario_id / ts
    out.mkdir(parents=True, exist_ok=True)
    (out / "log.json").write_text(json.dumps(
        {"scenario": scenario_id, "steps": result["steps"],
         "milestones": result["milestones"], "tokens": result["tokens"],
         "log": result["log"]}, indent=2))
    (out / "entities.json").write_text(json.dumps(result["entities"], indent=2))
    return out
