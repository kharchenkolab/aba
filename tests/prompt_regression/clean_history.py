"""Normalize stale recipe references in captured cases.

Captured messages were harvested before two structural changes:
  1. Skill envelope (Phase 1) replaced `read_skill(name=...)` with
     `Skill(skill=..., args=...)`.
  2. Several recipes were renamed `<name>` → `<name>-v2`.

Both leave V1-shaped artifacts in the case `messages`: historical
`tool_use(read_skill, name='X')` blocks reference V1 names, and the
paired `tool_result` blocks contain V1-labeled body JSON. When the
harness replays these cases, the model sees its own past calls using
old names — pollution that biases recipe-name selection on subsequent
turns.

This script rewrites cases in place to align with the live registry:
  - `tool_use(read_skill, name=X)` → `tool_use(Skill, skill=Y)` where
    Y is the current registry name for X (via base-name match).
  - The paired `tool_result.content` is re-rendered from the current
    registry body so what the model replays matches what a fresh call
    would return today.
  - `env_stubs.read_skill` (legacy stub) is dropped — the live harness
    now resolves Skill stubs from the registry directly.
  - `target_recipe` field is normalized to the current registry name.

Idempotent: re-running on already-clean cases is a no-op. A `.bak`
sibling is written on first rewrite so the original capture is
recoverable. Run `python clean_history.py --check` to dry-run.
"""
from __future__ import annotations
import argparse, glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = "/workspace/aba/backend"
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _base(name: str) -> str:
    return re.sub(r"-v\d+$", "", name or "")


def _live_name_for(stale: str, registry: dict) -> str | None:
    """Resolve a stale recipe name (e.g. 'scrna-qc-clustering') to the
    current registry name (e.g. 'scrna-qc-clustering-v2'). Match by base
    name first; fall back to exact match. None if not in registry."""
    if not stale:
        return None
    if stale in registry:
        return stale
    sb = _base(stale)
    for name in registry:
        if _base(name) == sb:
            return name
    return None


def _live_body_blob(spec, body: str) -> str:
    """The JSON-encoded shape `_invoke_skill_core` returns for a live Skill
    call — what the model would see today on a fresh Skill(skill=name)."""
    out = {
        "status": "ok",
        "name": spec.name,
        "description": spec.description,
        "when_to_use": spec.when_to_use,
        "requires_tools": list(spec.requires_tools),
        "capabilities_needed": list(spec.capabilities_needed),
        "produces": list(spec.produces),
        "resources": list(spec.resources),
        "body": body,
    }
    return json.dumps(out)


def _clean_case(case: dict, registry: dict, get_spec, *, check: bool) -> tuple[dict, list[str]]:
    """Return (rewritten_case, list_of_change_descriptions). Idempotent."""
    changes: list[str] = []
    new_case = json.loads(json.dumps(case))   # deep copy via JSON
    # 1. Normalize target_recipe.
    tgt = new_case.get("target_recipe")
    if tgt:
        live = _live_name_for(tgt, registry)
        if live and live != tgt:
            new_case["target_recipe"] = live
            changes.append(f"target_recipe: {tgt!r} → {live!r}")

    # 2. Drop env_stubs.read_skill (stub now resolves via registry).
    if isinstance(new_case.get("env_stubs"), dict) and "read_skill" in new_case["env_stubs"]:
        del new_case["env_stubs"]["read_skill"]
        changes.append("env_stubs.read_skill removed (resolved via registry)")

    # 3. Walk messages, rewrite tool_use + paired tool_result blocks.
    msgs = new_case.get("messages", []) or []
    # First pass: collect tool_use_id → (new_name, new_input, new_body_blob) mappings
    id_to_replacement: dict[str, tuple[str, dict, str]] = {}
    for m in msgs:
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            name = b.get("name")
            if name not in ("read_skill", "Skill"):
                continue
            inp = b.get("input") or {}
            stale = inp.get("name") or inp.get("skill")
            if not stale:
                continue
            live = _live_name_for(stale, registry)
            if not live:
                changes.append(f"  tool_use {name}({stale!r}) — no live equivalent, leaving as-is")
                continue
            spec = get_spec(live)
            if spec is None:
                continue
            new_body = (spec.body or "").replace("$ARGUMENTS", inp.get("args") or "")
            new_blob = _live_body_blob(spec, new_body)
            id_to_replacement[b.get("id", "")] = (live, new_blob)
            if name != "Skill" or stale != live:
                changes.append(f"  tool_use {name}({stale!r}) → Skill(skill={live!r})")

    # Apply rewrites
    for m in msgs:
        content = m.get("content")
        if not isinstance(content, list):
            continue
        if m.get("role") == "assistant":
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                rep = id_to_replacement.get(b.get("id", ""))
                if not rep:
                    continue
                live, _ = rep
                b["name"] = "Skill"
                b["input"] = {"skill": live, **({"args": (b.get("input") or {}).get("args")} if (b.get("input") or {}).get("args") else {})}
        elif m.get("role") == "user":
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_result":
                    continue
                rep = id_to_replacement.get(b.get("tool_use_id", ""))
                if not rep:
                    continue
                _, new_blob = rep
                # Replace the result content with the freshly-rendered live blob.
                # The result might be string or list[block] in the capture;
                # normalize to a single text block.
                b["content"] = new_blob

    return new_case, changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry-run; print diffs without writing")
    ap.add_argument("--cases", default="all")
    a = ap.parse_args()

    # Load the live skill registry.
    from core.skills.loader import register_skill_dir, _REGISTRY, get_skill
    _REGISTRY.clear()
    register_skill_dir(os.path.join(BACKEND, "system_bundle/skills/core"), visibility="always")
    register_skill_dir(os.path.join(BACKEND, "system_bundle/skills/recipes"), visibility="local")
    registry = dict(_REGISTRY)
    print(f"Live registry: {len(registry)} skills")

    case_files = (sorted(glob.glob(os.path.join(HERE, "cases", "*.json"))) if a.cases == "all"
                  else [os.path.join(HERE, "cases", c + ".json") for c in a.cases.split(",")])

    total_changed = 0
    for cf in case_files:
        case = json.load(open(cf))
        new_case, changes = _clean_case(case, registry, get_spec=get_skill, check=a.check)
        if not changes:
            print(f"[clean] {os.path.basename(cf)}: already clean")
            continue
        total_changed += 1
        print(f"[clean] {os.path.basename(cf)}:")
        for c in changes:
            print(f"        {c}")
        if not a.check:
            bak = cf + ".bak"
            if not os.path.exists(bak):
                with open(bak, "w") as f:
                    json.dump(case, f, indent=2)
            with open(cf, "w") as f:
                json.dump(new_case, f, indent=2)
            print(f"        → wrote {os.path.basename(cf)} (backup at .bak)")

    print(f"\n{total_changed} case(s) {'would be' if a.check else 'were'} modified.")


if __name__ == "__main__":
    main()
